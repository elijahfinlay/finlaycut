## Quality Evaluation Harness

This is the regression loop for Finlay Cut analysis quality. It evaluates the
backend against labeled fixtures so filler detection, silence detection, and
speaker separation can be tuned against measured misses instead of anecdotal
reports.

### What it measures

- `filler`: interval precision/recall/F1 for filler detections, including
  transcript-derived fillers and raw-audio hesitation recovery.
- `silence`: interval precision/recall/F1 for silence and quiet-speech
  detections after the same refinement passes used by the app.
- `speakers`: duration-weighted speaker accuracy from diarization segments and,
  optionally, word-level speaker accuracy if you provide labeled word indices.

### Run it

From the repo root:

```bash
npm run eval:quality
```

Bootstrap a starter fixture from the current Resolve timeline or current clip at
the playhead:

```bash
npm run eval:bootstrap
```

If Resolve has in/out marks set, the bootstrapper uses that range. Otherwise it
uses the current clip at the playhead. It writes:

- an audio excerpt under `backend/evals/generated`
- a generated transcript JSON
- a predictions JSON with current filler/silence/speaker outputs
- a disabled starter fixture under `backend/evals/fixtures`

The starter fixture is intentionally created with placeholder expectations copied
from the current model output. Review and correct those labels before enabling
the fixture for real grading.

Or directly from the backend:

```bash
cd backend
PYTHONPATH=. python3 -m evals.runner --fixtures evals/fixtures
```

Write full JSON results:

```bash
cd backend
PYTHONPATH=. python3 -m evals.runner \
  --fixtures evals/fixtures \
  --json-out /tmp/finlaycut-eval.json
```

If a fixture does not include a transcript, the runner will transcribe the
audio on demand. Add `--write-generated-transcripts` if you want those saved
next to the fixture file for later reuse.

### Fixture format

Put `*.json` fixtures under [backend/evals/fixtures](/Users/elijahfinlay/Desktop/Websites/finlaycut/backend/evals/fixtures).

Example:

```json
{
  "id": "interview_guest_001",
  "audio_path": "/absolute/path/to/interview.wav",
  "fps": 23.976,
  "language": "en",
  "notes": "Guest is primary speaker, interviewer is quiet off camera.",
  "filler": {
    "enabled": true,
    "match_tolerance_ms": 140,
    "minimum_f1": 0.9,
    "expected": [
      { "start": 1.42, "end": 1.63, "label": "filler" },
      { "start": 9.85, "end": 10.02, "label": "filler" }
    ]
  },
  "silence": {
    "enabled": true,
    "threshold_db": -40,
    "min_duration_ms": 300,
    "min_duration_frames": 14,
    "match_tolerance_ms": 160,
    "minimum_f1": 0.85,
    "expected": [
      { "start": 4.08, "end": 4.74, "kind": "silence" },
      { "start": 22.30, "end": 23.10, "kind": "quiet_speech", "optional": true }
    ]
  },
  "speakers": {
    "enabled": true,
    "num_speakers": 2,
    "minimum_accuracy": 0.9,
    "expected_segments": [
      { "start": 0.00, "end": 14.20, "speaker": "guest" },
      { "start": 14.20, "end": 16.80, "speaker": "interviewer" },
      { "start": 16.80, "end": 35.00, "speaker": "guest" }
    ]
  }
}
```

### Guidance

- Use short real clips first. A 20-60 second interview excerpt is enough to
  expose filler, silence, and speaker failures.
- Label the spans you actually care about, not the whole clip, if speed
  matters. Speaker precision will be harsher when the expected labels cover only
  part of the file, but speaker accuracy still reflects how much of the labeled
  span was assigned correctly.
- Keep fixtures focused. One fixture for filler-heavy speech, one for quiet
  interviewer bleed, one for tricky sentence-ending fricatives, and so on.
