# Finlay Cut Grading Guide

This is the exact manual workflow for grading one clip.

## Files you will use

- Fixture to edit:
  - `backend/evals/fixtures/timeline_8_clip.json`
- Speaker fixture to edit:
  - `backend/evals/fixtures/timeline_8_clip_speakers.json`
- Silence fixture to edit:
  - `backend/evals/fixtures/timeline_8_clip_silence.json`
- Transcript to reference while grading words:
  - `backend/evals/generated/timeline_8_clip.transcript.json`
- Current model output to compare against:
  - `backend/evals/generated/timeline_8_clip.predictions.json`

## Fastest workflow

1. Run:

   ```bash
   npm run eval:bootstrap
   ```

   That creates a starter fixture for the current Resolve clip or marked range.

2. Open the fixture JSON file under `backend/evals/fixtures`.

3. Open the matching transcript JSON under `backend/evals/generated`.

4. Edit the fixture so `expected` contains the truth, not the model guess.

5. Set the top-level `"enabled": true`.

6. Run:

   ```bash
   npm run eval:quality
   ```

7. Read the scores. Low recall means the tool missed things. Low precision means it found too many things that were not real.

## What each section means

### `filler`

Use this for real spoken filler words you want the tool to catch.

Example:

```json
{
  "start": 10.611,
  "end": 11.473,
  "label": "filler",
  "notes": "Um"
}
```

Meaning:

- `start`: when the filler begins, in seconds.
- `end`: when the filler ends, in seconds.
- `label`: always use `"filler"` here.
- `notes`: optional human note like `Um` or `uh`.

What to do:

- Keep real `um`, `uh`, `like`, `you know`, or similar fillers.
- Delete entries that are not actually filler.
- Add entries the model missed.

### `silence`

Use this for pauses or quiet off-camera speech you want evaluated.

Example:

```json
{
  "start": 22.30,
  "end": 23.10,
  "kind": "quiet_speech"
}
```

Meaning:

- `kind: "silence"` = a real pause with no useful speech.
- `kind: "quiet_speech"` = very quiet off-camera speech that should be treated like removable dead space.
- `optional: true` = good to catch, but not required for the test to count as a miss.

### `speakers`

Use this to label who is talking during time ranges.

Example:

```json
{
  "start": 14.2,
  "end": 16.8,
  "speaker": "interviewer"
}
```

Meaning:

- `speaker` can be any name you want, like `"guest"` or `"interviewer"`.
- `expected_segments` should be time ranges where that speaker is talking.
- `expected_word_speakers` is optional and only needed if you want to grade speaker labels on exact word indices.

## How to grade the current clip

Open:

- `backend/evals/fixtures/timeline_8_clip.json`

For the first filler baseline:

- `filler` is enabled
- `silence` is disabled
- `speakers` is disabled

That means you only need to review the filler rows.

For the second speaker baseline:

- open `backend/evals/fixtures/timeline_8_clip_speakers.json`
- `speakers` is enabled
- `expected_segments` are the real speaker turns

That file is for grading diarization, not filler detection.

For the silence baseline:

- open `backend/evals/fixtures/timeline_8_clip_silence.json`
- `silence` is enabled
- `expected` contains candidate silence ranges copied from the current model output
- delete the wrong silence rows
- keep the real silence rows
- add any missed silence rows
- when it looks right, change the top-level `"enabled"` from `false` to `true`

That file is for grading silence cuts only.

## How to find correct times

Open:

- `backend/evals/generated/timeline_8_clip.transcript.json`

Look inside `segments[].words[]`.

Each word has:

- `word`
- `start`
- `end`

If you see:

```json
{
  "word": "Um,",
  "start": 10.611,
  "end": 11.473
}
```

then the fixture entry should be:

```json
{
  "start": 10.611,
  "end": 11.473,
  "label": "filler"
}
```

## How to read the eval output

When you run:

```bash
npm run eval:quality
```

you will see something like:

- `P` = precision
- `R` = recall
- `F1` = overall balance of precision and recall

Interpretation:

- Low `R`: the tool missed real fillers.
- Low `P`: the tool marked too many non-fillers as fillers.
- Higher `F1`: better overall behavior.

## Rule of thumb

Start simple:

- first make one good filler-only fixture
- then add one silence-heavy fixture
- then add one speaker-labeled interview fixture

That is much easier than trying to perfectly label everything at once.

## Silence grading for beginners

If you only want to grade silence, do this:

1. Open `backend/evals/fixtures/timeline_8_clip_silence.json`
2. Ignore the `filler` and `speakers` sections
3. Look only at `silence.expected`
4. Each row is one pause the tool thinks is silence
5. If that row is a real pause, keep it
6. If that row is not a real pause, delete it
7. If the tool missed a real pause, add another row with:

```json
{
  "start": 12.34,
  "end": 12.88,
  "kind": "silence"
}
```

8. If it is very quiet off-camera speech that you still want removed, use:

```json
{
  "start": 22.30,
  "end": 23.10,
  "kind": "quiet_speech"
}
```

9. Change the file's top-level `"enabled"` to `true`
10. Run:

```bash
npm run eval:quality
```

The result tells you:

- low precision = it is flagging too many things as silence
- low recall = it is missing real silences
