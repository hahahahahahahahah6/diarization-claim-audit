# diarization-claim-audit

Version 0.1.0 · MIT · Python 3.9+

An offline Python CLI that audits documented speaker diarization claims against
static Python source evidence. It reads files locally, parses source with AST,
and never imports or executes the audited project. No network or telemetry.
It does not process recordings or evaluate model accuracy.

## Install and use

```sh
python3 -m pip install .
diarization-claim-audit /path/to/project
python3 -m diarization_claim_audit /path/to/project --json
python3 -m diarization_claim_audit /path/to/project --verbose
python3 -m diarization_claim_audit /path/to/project --ignore vendor --ignore build --fail-on MEDIUM
python3 -m diarization_claim_audit --version
```

The positional root must be a directory. `--ignore` is repeatable and excludes
relative path prefixes at path-component boundaries. `--verbose` shows evidence
locations; `--json` emits status, findings, production evidence and test evidence.
`--fail-on` accepts LOW, MEDIUM or HIGH, default HIGH. Version output is `0.1.0`.

Exit codes:

- **0**: PASS, or findings below the selected failure threshold.
- **2**: at least one finding meets or exceeds the threshold.
- **1**: usage or I/O error, including bad paths and invalid arguments.

A JSON status of FAIL means findings exist even when the threshold permits exit 0.

## Rules and documentation

Documentation includes root README.md and Markdown files anywhere under a `docs`
path component. Backtick and tilde fenced blocks with up to three leading spaces
are excluded; inline code counts. Paragraphs split on blank lines. Matching
lowercases text, expands `n't` to ` not`, replaces hyphens with spaces and
collapses whitespace. Exact phrases use word boundaries, longest first with
alphabetical ties. Negative spans are removed before positive matching. Both
US and UK spellings are recognized. Vocabulary lives in the package's phrases.py.

```text
documented-speaker-diarization-claim-false
  HIGH: positive claim without production evidence
  MEDIUM: evidence exists only in tests
documented-no-speaker-diarization-claim-false
  HIGH: negative claim with production evidence
```

There is at most one finding per rule family. With no Python files, a positive
claim yields HIGH; a negative claim passes.

## Source evidence

Each Python module is analyzed independently, resolving import aliases and
attribute forms. Local definitions shadow imported names. Recognized calls:

```text
pyannote: Pipeline.from_pretrained with a literal string argument containing
          diarization or diarisation (case insensitive)
pyannote: SpeakerDiarization construction
whisperx: DiarizeModel construction or assign_word_speakers call
nemo: NeuralDiarizer construction
simple_diarizer: Diarizer construction
any base: .diarize(...) unless a local function or method shadows diarize
```

Production excludes fixtures, docs and examples path components, plus tests
components and files named test_*.py or *_test.py. Tests count separately;
fixtures, docs and examples never supply evidence. Symlinked files and directories
are skipped. Invalid syntax supplies no evidence. Unreadable files are I/O errors.

Bare imports, name references, unresolved constructor calls, string mentions and
subprocess invocations do not count. Neither do transcription, known-person
verification, embeddings, clustering, voice activity detection, audio language
identification or audio generation alone. Pipeline loads without the required
literal model string do not count.

## Neighbours

- voice-claim-audit checks transcription and synthesis. Its verdict is independent:
  a transcribe call is insufficient here, and a diarization call is insufficient there.
- speaker-claim-audit checks audio playback through speakers; playback is insufficient here.
- microphone-claim-audit checks capture, rather than segmentation.
- clustering-claim-audit checks generic clustering. Clustering embeddings alone is insufficient here.
- language-detection-claim-audit checks text language identity. Audio language
  identification is insufficient here.
- Translation, summarization, question-answering and sentiment families check text
  operations rather than segmentation of audio by who spoke.

## Limitations

This is a bounded static heuristic, not proof of working audio behavior. Constructors
count without inference, and generic attribute calls can produce false positives.
Dynamic imports, wrappers, cross-module data flow, runtime aliases and external
executables are outside its resolution model. Shadows are conservatively collected
across the entire module, including nested definitions, without execution-order
analysis. Only listed phrases and listed AST fingerprints are recognized.

The tool's own self-audit passes because its source dispatcher uses a generic
`.diarize(...)` call. That illustrates the heuristic's limits; the dispatcher
performs static evidence analysis, not audio processing.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile diarization_claim_audit/__init__.py diarization_claim_audit/__main__.py diarization_claim_audit/audit.py diarization_claim_audit/phrases.py tests/test_audit.py
python3 -m build
python3 -m diarization_claim_audit .
```
