# process-show v1 interface

## Local scope, evaluation catalog and ports

The three launchers share tools/entry.py and start only a local service. The final root is the plugin parent directory. Native configuration is generated/rebound locally; outside-root source paths and symlink/junction escapes are rejected. No SSH or cross-OS mapping is required by these launchers.

`GET /api/runs` returns `runs`, `expired_runs`, `ttl_seconds`, `pending`, and a scanned timestamp. The scan is asynchronous; cached rows may be returned while refreshing. Rows contain an opaque ID, date/model/weight summary, dataset, completed/expected counts, source-record status, expiry and retention. SDK-only collected runs are also listed. An expired unretained row is excluded.

`POST /api/runs/select` accepts only `{"id":"catalog-id"}`. It rejects stale, expired or foreign identities and returns `selected`, `url`, `port`, and `entry_port`. Each evaluation gets its own free loopback port. Repeated open reuses that evaluation's listener. Bound ports filter /api/episodes and reject reads/mutations for other evaluations. One project store/retention policy and encoder are shared. Local port registrations live under outputs/process-show, never in a global registry.

`GET /api/health` identifies project_root/project_id/scope_id/service_port/run_id. `POST /api/control/stop` requires scope_id and same-origin JSON. On an evaluation port it closes that evaluation listener; on the directory control port it stops the directory service and records manual stop. It never stops model evaluation. Explicit start resumes; automatic SDK startup respects manual stop.

`process-show/tools/evaluate.py SCRIPT.py [args]` is the optional local evaluation wrapper. SCRIPT must be inside the current final root. An unmodified model must integrate the SDK/JSONL/hook to capture missing telemetry; a folder alone cannot recover nonexistent images.

Replay includes actual reset start and explicitly supplied or SHA-verified annotation goals. Unknown goals remain unknown; terminal position is not a substitute. These data remain viewer-only. Agent follow translates the view without automatically increasing zoom. Intermediate arrivals are small dots with no printed step IDs. RGB decoding and BEV commit to the same displayed step.

## Presentation v2 additions

Event schema remains v1. The browser now uses `GET /api/episodes/{id}/replay` (optional `after=N`) and `GET /api/episodes/{id}/observation?step=N&strict=1` instead of full-composite frame RPC while scrubbing. Replay includes `observation_step`, the latest recorded step <= the requested state. Original `rgb_available` is unchanged; fallback does not create a new observation. Image responses expose `X-Observation-Step` and `X-Requested-Step`, with private browser caching.

`GET /api/episodes/{id}/map?height=Y&mode=scene&step=N` returns verified scene geometry metadata and image URL; `mode=nav` returns the navigable-area layer. Case1 uses the run manifest's exact scene/navmesh identities. Canonical input paths must stay inside the protected case view. CPU GLB z-buffer rendering cuts the roof 1.7m above the chosen height; MP3D [x,y,z] source coordinates map to Habitat [x,z,-y], verified against navmesh X/Z bounds before publication. It includes actual furniture/wall surfaces; Basis textures currently use height shading. `.scene_geometry` is optional and imports numpy only on use.

Generic SDK `map_image` inputs are saved once per content hash, paired with `map_transform`; the browser supports rotated/reflected 2x3 affine transforms. The map endpoint serves this supplied map without Habitat. `/map-image?step=N` only serves the episode's registered map asset.

`POST /api/episodes/{id}/render` with `{}` requests a v4 MP4. Incomplete episodes are rejected; ready/error completed episodes may be regenerated. User requests take priority at a safe preview boundary. Old video bytes remain until the replacement passes full decode validation. `episode.meta.video_version=4` and `encoding.json.presentation_version=4` confirm the new export.

SDK recordings retain compact WebP observation assets for immediate browsing after video encoding; full-size composite previews are still removed after successful encoding. Retention removes both together. Case1 imports reference original input images read-only, without copying them.

## Python SDK

```python
from process_show import Recorder
recorder = Recorder('/absolute/path/outputs/process-show', queue_size=128)
episode_key = recorder.start_episode(metadata)
accepted = recorder.record_step(step, rgb=rgb_array_or_PIL, bev=map_array_or_PIL)
recorder.end_episode(result)
recorder.close()
```

Main process creation must be guarded by `if __name__ == '__main__':` for multiprocessing spawn. One Recorder per evaluator worker can handle successive episodes. `record_step` snapshots mutable image arrays and uses a bounded nonblocking queue; `False` means a dropped frame. Terminal messages are sent on a separate pipe and processed after accepted frames drain. `close()` waits up to 60 seconds; timeout raises without killing the consumer. On evaluator crashes, incomplete recordings remain live and visible rather than being relabelled completed.

The independent `serve` process encodes completed episodes. SDK does not directly run FFmpeg or wait for encoding in model inference. Storage/render failures are visible in episode.error. Dropped steps become explicit gap frames at finalization, never interpolated observations.

### Metadata

Required: `run_id`, `attempt`, `model`, `stage`, `dataset`, `split`, `scene_id`, `episode_id`, `instruction` (empty string permitted if genuinely unavailable). Optional: `schema_version=1`, `instruction_id`, `started_at` (timezone-aware ISO; defaults to capture start), `language`, `provenance`, `map_transform`.

Identity hashes run/attempt/model/dataset/split/scene/episode/instruction ID. Retrying an episode requires a new attempt. Same identity with changed instruction/stage/coordinate system is rejected. Date groups use Asia/Singapore even when timestamps are supplied in UTC.

### Step

`step`: nonnegative integer, reset=0; `action`: required after reset; `timestamp`: UNIX seconds; `observation_phase`: reset or after_action. Optional `pose={position:[x,y,z],rotation_xyzw:[x,y,z,w]}`, `collided`, `call_id`, `observation_call_id`, `raw_output`, `floor_id`, `map_transform`.

The canonical coordinate system is meters, x-right/y-up/z-back, camera forward=-z, quaternion xyzw. Other conventions must be converted by the adapter and are rejected if declared without conversion. A supplied `map_transform` is a finite 2x3 affine matrix mapping world [x,z,1] to source image [u,v]. Map image and transform must describe the same floor. No floor ID means explicitly unlayered trajectories with height shown.

Record the observation AFTER each actual action, before auto-reset. The current Case1 environment is auto_reset_false. For vectorized auto-reset systems, use the terminal observation provided by the environment; do not substitute the next episode's reset frame.

### Result

Required: `total_steps`, `status` (`completed`, `interrupted`, `error`). Optional `success` (boolean or null), `termination_reason`, `metrics`, `metrics_source`, `model_calls`. Only completed + explicit success boolean participates in success/failure retention. STOP counts if actually executed. Step limits are not actual totals.

## Generic JSONL

Alternative for any programming language: one complete UTF-8 JSON object per newline, one episode per file. Place image paths under the explicitly configured input root. Absolute escapes and symlinks are refused. Start must precede steps, end seals the episode. Append-only files may be tailed; a partial final line is deferred, an invalid completed line is an error.

```json
{"schema_version":1,"type":"start","metadata":{"run_id":"r1","attempt":"1","model":"MyModel","stage":"eval","dataset":"R2R","split":"val_unseen","scene_id":"scene1","episode_id":"123","instruction":"Walk forward."}}
{"schema_version":1,"type":"step","data":{"step":0,"action":"RESET","rgb_path":"images/reset.png","pose":{"position":[0,0,0],"rotation_xyzw":[0,0,0,1]}}}
{"schema_version":1,"type":"step","data":{"step":1,"action":"STOP","rgb_path":"images/stop.png","pose":{"position":[0,0,0],"rotation_xyzw":[0,0,0,1]}}}
{"schema_version":1,"type":"end","result":{"total_steps":1,"status":"completed","success":false,"termination_reason":"stop"}}
```

`watch/convert --adapter generic --run-root ROOT --stage eval --model MyModel` discovers ROOT/*.jsonl. Metadata within the stream is authoritative. Generic source files are not deleted by retention; only plugin-generated previews/videos are managed.

## HTTP (localhost only)

| Method | Path | Meaning |
|---|---|---|
| GET | /api/episodes | catalog, metadata, maximum received step, retention states |
| GET | /api/episodes/{hash} | episode metadata/result/error |
| GET | /api/episodes/{hash}/steps | steps and sealed step-to-time index |
| GET | /api/episodes/{hash}/frame?step=N | exact recorded step composite; ready video uses precise decoding |
| GET | /api/episodes/{hash}/video?download=1 | MP4 attachment; HTTP byte ranges and protected download lease |
| GET | /api/episodes/{hash}/thumbnail | thumbnail |
| GET | /api/events | SSE update notifications and reconnect support |
| POST | /api/episodes/{hash}/pin | JSON `{"pinned":true}` |
| GET/POST | /api/policy | inspect/update count, per-dataset overrides, selections, rules, TTL and storage budget |

No generic filesystem download endpoint. Mutations require same-origin JSON; nonlocal Host headers are refused. The service binds loopback for local use; no public binding is configured. `episode.source_run` is local provenance, not an arbitrary file access grant.

Policy patch example:

```json
{"count":5,"selections":{"R2R":["1107","1563"]},"datasets":{"RxR":20},"rules":[{"dataset":"R2R","scene_id":"scene1"},{"stage":"my-stage"}],"ttl_seconds":3600,"max_bytes":21474836480}
```

Rules are ORed; fields inside each rule are ANDed exact string matches. Permitted fields: dataset/episode_id/scene_id/stage/run_id. Empty rules rejected. Pins and rule matches count toward quota but override its upper bound. Raising count or changing rules cannot restore expired assets. `cleanup --dry-run` updates current selection but deletes no file.

## Output contract

`DATE-STAGE-DATASET/DATASET-SPLIT-SCENE-epID-instID-runHASH-attemptATTEMPT/` holds named MP4, episode.json, result.json, steps.json, thumbnail.webp, encoding.json. Active `previews/STEP-PAGE.webp` are removed only after complete MP4 decode validation. Longer instructions use 2 seconds per page and explicit step time intervals; normal steps use 0.5 seconds. `catalog.sqlite3` coordinates import cursors, random priorities, policy, owned files and leases.

Truncated/replaced sources require distinct attempts. Source trace SHA is recorded in QA receipts; formal model identity is copied from existing run manifest where available. Navigation metric recalculation is outside this plugin.

## Sources

- [Habitat visualization](https://github.com/facebookresearch/habitat-lab/blob/main/examples/tutorials/nb_python/Habitat_Lab_TopdownMap_Visualization.py)
- [VLN-CE trainer](https://github.com/jacobkrantz/VLN-CE/blob/master/vlnce_baselines/common/base_il_trainer.py)
- [Rerun timeline](https://rerun.io/docs/reference/viewer/timeline)
- [MDN seeking](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Audio_and_video_delivery/buffering_seeking_time_ranges)
- [Noto CJK OFL](https://github.com/notofonts/noto-cjk/blob/main/Sans/LICENSE)

### Explicit trajectory selection

`selections` maps dataset name to string Episode IDs. Patch one dataset by merging the existing selections object; empty list removes that dataset's explicit selections, without removing manual pins or rules. Duplicates are normalized. All runs/attempts with a matching dataset and Episode ID are kept; use a pin for one specific run. Eligible completed success/failure records randomly fill remaining quota (default 5) with persistent priorities. Explicit records above quota all remain. Missing/expired IDs are remembered for future imports but do not reserve empty slots or trigger reconstruction. UI reports unmatched IDs and current selected count.

Video presentation v4 removes numbered BEV markers and automatic agent crop; existing v3 videos remain until the user requests “生成新版 MP4”.

## Source refresh and history loading

`GET /api/sync` returns configured_sources, expired_episodes and per-source history_enabled, phase, discovered, indexed, pending, excluded_history, expired, last_scan, last_progress, errors and limit. Counts are asynchronous snapshots, not evaluation scores. `POST /api/refresh` accepts only `{"history":true}` (boolean, default true), persists the per-source history preference, wakes source workers and returns 202. Arbitrary source paths and cross-origin requests are rejected. No source cursor is reset, no expired episode is re-created. Watchers retain one writer per source; active files are prioritized, history queue rotation survives discovery scans. Errors retry with bounded backoff; manual refresh retries immediately. Sources are discovered every five seconds, independent from the browser's configurable polling interval.

### Nonblocking map responses

GET episode map may return `available:false,pending:true,state:queued|running|busy,reason,retry_after`. Poll after retry_after seconds; available:false without pending is an explicit unavailable/error result. Existing verified disk maps bypass the generation lock and job queue. Uncached map generation uses one daemon worker with 8 waiting slots and deduplicates source/scene/height/layer keys. UI cancels stale polling on selection changes and exposes a map-only retry.

## User-selectable cache and local save

`POST /api/policy` accepts `ttl_seconds` from 60 to 2592000 (1 minute..30 days), default 3600. Existing unretained completed videos use the updated duration from their successful MP4 commit. Fixed/selected content, unfinished work, and download leases remain protected. Shortening may cause next-cleanup expiry; deleted assets are not reconstructed.

“保存到…” either pins an episode in the current project output or streams the MP4 to the browser File System Access save picker. Unsupported browsers use the standard MP4 attachment download. The server receives no local target directory and exposes no arbitrary filesystem writer.

Implementation is grouped under capture/storage/playback/media/maps/service/web. Published imports such as `process_show.sdk` and `process_show.core` remain aliases to canonical grouped modules through the package-local compat folder.

## Client-side port verification

Selection responses also carry scope_id and a per-listener probe_token. A browser may GET the returned URL's /api/health?probe=TOKEN; only a valid token and HTTP localhost/127.0.0.1 Origin receive a narrow health-response CORS grant. The response identifies scope_id and evaluation_id. Other endpoints do not inherit that grant. The UI refuses to navigate if the host is unreachable or either identity differs.
