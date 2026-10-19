# Dataset card — AEGIS AI training dataset {{version}}

> Fill nothing in by hand: this card is rendered by
> `app.ml.datasets.auto_labeler.build_dataset()` from the run's real
> statistics. Every number below was measured, not estimated.

## Collection

| Field | Value |
|---|---|
| Dataset version | {{version}} |
| Collected by | {{collector}} |
| Created (UTC) | {{created}} |
| Match | {{match_id}} |
| Source video | {{video}} |
| Notes | {{notes}} |

## Samples

- **Labeled frames:** {{n_samples}}
- **Events in the log:** {{n_events}}
- **Events that became labels:** {{n_labeled_events}}
- **Events skipped:** {{n_skipped}}

### Label breakdown (events, not frames)

{{label_breakdown}}

### Skipped events (with reasons — never silently dropped)

{{skip_breakdown}}

## Label source

{{label_source}}

## Join parameters

- Event↔frame window: ±{{window_s}}s
- Event timestamp offset applied: {{ts_offset}}s
- Timestamp gaps >3× median frame interval in the recording: {{gaps}}

## Honest caveats

- **Label quality = spotter accuracy.** A mistapped killer/victim name or a
  late tap becomes a wrong label. Garbage in, garbage out — review
  `stats.json` skip reasons and spot-check `frames/` before training.
- Spotter taps lag what they saw; if the observer phone (spectator mode)
  and the recording PC clocks differ, re-run the labeler with a
  `--ts-offset` correction instead of accepting misaligned labels.
- Frames are sampled around logged events only. "Quiet" gameplay between
  events is not represented — this dataset teaches *what just happened*,
  not ambient scene understanding.
