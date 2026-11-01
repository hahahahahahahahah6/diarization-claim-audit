import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from diarization_claim_audit.audit import audit, claims, main, source_evidence
from diarization_claim_audit.phrases import DIAR, NODIAR


class EvidenceTests(unittest.TestCase):
    pass


POSITIVE = [
    'import pyannote.audio; pyannote.audio.Pipeline.from_pretrained("speaker-diarization")',
    'import pyannote.audio as p; p.Pipeline.from_pretrained(model="DIARISATION")',
    'from pyannote.audio import Pipeline as P; P.from_pretrained("diarization")',
    'from pyannote.audio import pipelines as p; p.SpeakerDiarization()',
    'from pyannote.audio.pipelines import SpeakerDiarization as S; S()',
    'import whisperx; whisperx.diarize.DiarizeModel()',
    'from whisperx.diarize import DiarizeModel as D; D()',
    'import whisperx as w; w.assign_word_speakers(a, b)',
    'from whisperx import assign_word_speakers as a; a(x, y)',
    'import nemo.collections.asr.models as n; n.NeuralDiarizer()',
    'from nemo.collections.asr.models import NeuralDiarizer as N; N()',
    'import simple_diarizer.diarizer as s; s.Diarizer()',
    'from simple_diarizer.diarizer import Diarizer as D; D()',
    'pipeline.diarize(audio)',
    'factory().diarize(audio)',
    'diarize = 1; pipeline.diarize(audio)',
]
NEGATIVE = [
    'import pyannote.audio; import whisperx',
    'from pyannote.audio import Pipeline; Pipeline.from_pretrained("segmentation")',
    'from pyannote.audio import Pipeline; Pipeline.from_pretrained(model)',
    'import whisper; whisper.transcribe(audio)',
    'import speech_recognition as s; s.Recognizer().recognize_google(audio)',
    'from transformers import pipeline; pipeline("automatic-speech-recognition")',
    'from speechbrain.inference.speaker import SpeakerRecognition; SpeakerRecognition(); model.verify(a,b)',
    'import resemblyzer; resemblyzer.VoiceEncoder().embed_utterance(audio)',
    'from sklearn.cluster import KMeans; KMeans().fit(embeddings)',
    'import hdbscan; hdbscan.HDBSCAN().fit(embeddings)',
    'import webrtcvad; webrtcvad.Vad().is_speech(audio, 16000)',
    'model.detect_language(audio)',
    'engine.synthesize(text)',
    'x = "pyannote.audio.Pipeline.from_pretrained diarization"',
    'import subprocess; subprocess.run(["diarize", "audio.wav"])',
    'from whisperx import DiarizeModel; DiarizeModel',
    'diarize(audio)', 'DiarizeModel()', 'NeuralDiarizer()', 'Diarizer()', 'SpeakerDiarization()',
    'from other import Diarizer; Diarizer()',
    'from whisperx import DiarizeModel\ndef DiarizeModel(): pass\nDiarizeModel()',
    'from whisperx import DiarizeModel as D\nclass D: pass\nD()',
    'from whisperx import DiarizeModel as D\nD = factory\nD()',
    'from whisperx import DiarizeModel as D\ndef f(D): D()',
    'def diarize(): pass\npipeline.diarize(audio)',
    'class C:\n def diarize(self): pass\npipeline.diarize(audio)',
    'from pyannote.audio import Pipeline\ndef Pipeline(): pass\nPipeline.from_pretrained("diarization")',
    'import whisperx\ndef whisperx(): pass\nwhisperx.DiarizeModel()',
    'import whisperx\nwhisperx.DiarizeModel((',
]


def evidence_test(code, expected):
    def test(self):
        self.assertEqual(bool(source_evidence(code)), expected, code)
    return test


for index, code in enumerate(POSITIVE):
    setattr(EvidenceTests, 'test_positive_%02d' % index, evidence_test(code, True))
for index, code in enumerate(NEGATIVE):
    setattr(EvidenceTests, 'test_negative_%02d' % index, evidence_test(code, False))


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def run_cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            try:
                code = main(list(args))
            except SystemExit as error:
                code = error.code
        return code, output.getvalue()

    def test_all_phrases(self):
        self.assertEqual(len(DIAR), 48)
        self.assertEqual(len(NODIAR), 31)
        for phrase in DIAR:
            self.assertEqual(claims(phrase), ([phrase], []))
        for phrase in NODIAR:
            self.assertEqual(claims(phrase), ([], [phrase]))

    def test_longest_and_boundaries(self):
        self.assertEqual(claims('supports speaker diarization'), (['supports speaker diarization'], []))
        self.assertEqual(claims('xspeaker diarizationx'), ([], []))

    def test_fences_and_inline(self):
        self.assertEqual(claims('```text\nspeaker diarization\n```\n~~~\nspeaker labels\n~~~'), ([], []))
        self.assertEqual(claims('   ````\nspeaker labels\n```\nspeaker labels\n````'), ([], []))
        self.assertEqual(claims('`speaker diarisation`'), (['speaker diarisation'], []))

    def test_normalization(self):
        self.assertEqual(claims("DOESN'T diarise-speakers"), ([], ['does not diarise speakers']))
        self.assertEqual(claims('Multi-speaker\n transcription'), (['multi speaker transcription'], []))
        self.assertEqual(claims('speaker\n\ndiarization'), ([], []))

    def test_no_source_positive(self):
        self.put('README.md', 'speaker diarization')
        self.assertEqual(audit(self.root)['findings'][0]['severity'], 'HIGH')

    def test_no_source_negative(self):
        self.put('README.md', 'no speaker diarization')
        self.assertEqual(audit(self.root)['status'], 'PASS')

    def test_production(self):
        self.put('README.md', 'speaker diarization')
        self.put('app.py', 'model.diarize(audio)')
        self.assertEqual(audit(self.root)['status'], 'PASS')
        self.put('README.md', 'no speaker diarization')
        self.assertEqual(audit(self.root)['findings'][0]['rule'], 'documented-no-speaker-diarization-claim-false')

    def test_test_only(self):
        self.put('README.md', 'speaker diarization')
        for name in ['tests/a.py', 'test_app.py', 'app_test.py']:
            self.put(name, 'model.diarize(audio)')
        result = audit(self.root)
        self.assertEqual(result['findings'][0]['severity'], 'MEDIUM')
        self.assertEqual(len(result['test_evidence']), 3)
        self.assertEqual(self.run_cli(str(self.root))[0], 0)
        self.assertEqual(self.run_cli(str(self.root), '--fail-on', 'MEDIUM')[0], 2)
        self.assertEqual(self.run_cli(str(self.root), '--fail-on', 'LOW')[0], 2)

    def test_excluded_evidence(self):
        self.put('README.md', 'speaker diarization')
        for name in ['fixtures/a.py', 'nested/docs/a.py', 'examples/a.py', 'docs/tests/a.py']:
            self.put(name, 'model.diarize(audio)')
        self.assertEqual(audit(self.root)['findings'][0]['severity'], 'HIGH')

    def test_docs_component(self):
        self.put('nested/docs/sub/guide.md', 'speaker diarisation')
        self.put('other.md', 'no speaker diarization')
        result = audit(self.root)
        self.assertEqual(len(result['findings']), 1)
        self.assertEqual(result['findings'][0]['claims'][0]['path'], 'nested/docs/sub/guide.md')

    def test_symlinks(self):
        self.put('README.md', 'speaker diarization')
        target = self.put('fixtures/real.py', 'model.diarize(audio)')
        (self.root / 'linked.py').symlink_to(target)
        (self.root / 'linked').symlink_to(target.parent, target_is_directory=True)
        self.assertEqual(audit(self.root)['findings'][0]['severity'], 'HIGH')
        (self.root / 'loop').symlink_to(self.root, target_is_directory=True)
        self.assertFalse(audit(self.root)['evidence'])

    def test_symlink_docs(self):
        target = self.put('other.md', 'speaker diarization')
        (self.root / 'README.md').symlink_to(target)
        self.assertEqual(audit(self.root)['status'], 'PASS')

    def test_syntax_invalid(self):
        self.put('README.md', 'speaker diarization')
        self.put('app.py', 'model.diarize(audio)\n(')
        self.assertFalse(audit(self.root)['evidence'])

    def test_module_isolation(self):
        self.put('README.md', 'speaker diarization')
        self.put('a.py', 'from whisperx import DiarizeModel')
        self.put('b.py', 'DiarizeModel()')
        self.assertFalse(audit(self.root)['evidence'])

    def test_cli_json_ignore_verbose(self):
        self.put('README.md', 'speaker diarization')
        self.put('src/app.py', 'model.diarize(audio)')
        self.put('src_extra/a.py', 'pass')
        code, output = self.run_cli(str(self.root), '--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['status'], 'PASS')
        self.assertEqual(self.run_cli(str(self.root), '--ignore', 'src', '--ignore', 'unused')[0], 2)
        self.assertIn('generic diarize call', self.run_cli(str(self.root), '--verbose')[1])
        self.assertEqual(self.run_cli(str(self.root), '--ignore', 'README.md')[0], 0)

    def test_cli_errors_version(self):
        for args in [[], ['missing-path'], [str(self.root), '--unknown'], [str(self.root), '--fail-on', 'BAD']]:
            self.assertEqual(self.run_cli(*args)[0], 1)
        self.assertEqual(self.run_cli('--version'), (0, '0.1.0\n'))

    def test_decode_error(self):
        (self.root / 'README.md').write_bytes(b'\xff')
        self.assertEqual(self.run_cli(str(self.root))[0], 1)


if __name__ == '__main__':
    unittest.main()
