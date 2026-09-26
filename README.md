# GPT-6 Astra + Jev play Left 4 Dead 2

An original Windows controller, observation addon and recording/editing tools from a completed run of **all five original Left 4 Dead 2 campaigns: 23 chapters, on Easy, in private single player with bot teammates**.

[Five-minute winning-finale video and result](https://x.com/imjustnewatai/status/2103613842999910893)

Jev made fast task and combat choices. GPT-6 Astra planned, inspected game screenshots, repaired failures and supervised recovery. Python handled navigation, aiming and ordinary keyboard/mouse inputs. The controller used an original VScript observation addon for local game state and navigation information. **This was telemetry-assisted play, not a vision-only run, uninterrupted speedrun or unsupervised agent benchmark.**

## What actually happened

| Campaign | Verified result and retry notes |
| --- | --- |
| Dead Center | 4/4 chapters. Finale: 24 activated attempts, plus one interrupted run before activation. Exact early-chapter totals are incomplete. |
| Dark Carnival | 5/5. Fairgrounds took two attempts; concert finale took one. The controlled survivor was incapacitated when the escape counted. |
| Swamp Fever | 4/4. Swamp and Plantation each took two attempts. |
| Hard Rain | 5/5, each on its first attempt. |
| The Parish | 5/5. Park took two attempts. Bridge: 13 runs, consisting of 11 losses, one engine crash interruption and the winning escape. |

The winning Bridge round lasted **13m33s of game time**. Across the project, the logs establish **at least 13.8 hours of controller play**, including retries; missing intervals and game-clock resets prevent an exact whole-run total. There were **21.9 hours of raw capture**, including pauses and setup. These are different measures. The Jev ledger totaled **$12.27** using the run's configured token-price accounting; this is not a provider invoice. Astra's cost was not calculated.

The final film uses the successful attempts, with pauses and selected waiting/stalls cut. Retry counts remain disclosed. New editorial text is labeled retrospective commentary; no private reasoning or invented live quotes are included.

The playful paranoid part was practical: Is the recorder advancing? Are we repeating a failed move? Did the engine actually report an escape? Recorder checks, loop handoffs and real win events mattered throughout the run.

## Source map

| Area | Entry points |
| --- | --- |
| Jev decision client, reservation ledger and public commentary | `jev_bridge.py` |
| Asynchronous decision/motor loop and bounded handoff | `agent_controller.py` |
| Task candidates, confidence checks and tactical policy | `agent_policy.py`, `agent_state.py` |
| Movement, aiming, rescue, interaction and combat skills | `agent_motor.py`, `local_motion.py` |
| Local navigation, mission progression and fuel objectives | `navigation.py`, `mission_planner.py`, `fuel_planner.py`, `game_knowledge.py` |
| Foreground ownership and ordinary Windows input | `game_input.py` |
| Original observation addon and deployment | `sensor/`, `build_sensor.py`, `deploy_sensor.py`, `observe_game.py` |
| Game-only capture checks and isolation test | `prepare_capture.py`, `obs_guard.py`, `capture_isolation_test.py`, `analyze_isolation.py` |
| Astra screenshot facts with freshness checks | `vision_observer.py` |
| Recorded-footage inventory, edit assembly and review tooling | `inventory_footage.py`, `build_edit_candidates.py`, `build_edit_timeline.py`, `render_story_export.py`, `scan_*.py`, `transcribe_timeline_audio.py` |
| Regression cases for observed failures | `test_general_agent.py`, `test_capture_lifecycle.py`, `test_cursor_boundary.py` |

`controller.py` is the earlier pilot controller; `agent_controller.py` is the later general controller. The abandoned local-vision experiment is retained as source in `local_vision.py` and `benchmark_local_vision.py`, but is not connected to the final controller and includes no models. No training or fine-tuning was performed.

## Reproduction status

This is a research source release, not a one-command guaranteed replay. The run involved live Astra supervision and improvements to the harness. Private mission directives, telemetry, account state and raw media are intentionally absent. Build a fresh local session and validate it before trusting a long run. The regression suite uses synthetic/mocked state and does not establish that a new computer can finish the game unattended.

The core environment used Windows, 64-bit Python 3.12, the Steam version of Left 4 Dead 2, a portable OBS installation with game-process audio capture, and Typesafe's `jev-1.13.0` API. The encoder used NVIDIA NVENC. No game files, OBS binaries, model weights, account credentials or recordings are redistributed.

### Core setup

Create a separate checkout and environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest test_general_agent test_capture_lifecycle test_cursor_boundary
```

`L4D2_GAME_DIR` can point to the game's `left4dead2` subdirectory. If omitted, code uses Steam's standard Windows installation path. Use your own installation; no save or account state from the original run is needed or supplied.

The addon observes game state and writes local JSON under `ems/astra`. It does not set health, teleport, change combat rules or complete objectives. With the game stopped, `build_sensor.py` builds the original scripts into `addons/astra_observer.vpk`; `deploy_sensor.py` installs both live sensor and metrics scripts. These commands write to your game directory. `research_game.py` reads locally installed Valve map assets into private research files; those derived files are not part of this release.

### Recording before control

Place your own portable OBS installation under `tools/obs-portable`. `prepare_capture.py` generates a separate profile and random local WebSocket credential but does **not** start recording. It refuses to overwrite an existing generated credential file.

Configure its `L4D2 Game Only` collection and `Game only` scene with one source called `L4D2 only`: game capture targeting the exact Left 4 Dead 2 window, process audio enabled, overlays/cursor disabled, no filters, no desktop audio or microphone, no audio monitoring, 1280x720 at 30 fps. `obs_guard.py` describes and checks the exact accepted settings; it rejects extra inputs rather than falling back to a desktop or system-audio source. A minimum 5 GiB free-space reserve is enforced.

Apply `astra_privacy.cfg` in your private/local game, and independently disable account overlays/notifications. The config disables game voice input/output and enables local-only play. Run the controlled isolation test and inspect the recording before ordinary play. A valid configuration alone does not prove that a recording contains only the intended audio.

Game input requires the explicitly selected foreground process and a healthy advancing recorder. Do not run two controllers at once. Capture finalization requires a confirmed pause or process exit. A handoff requests an actual game screenshot for the planner; it does not automatically establish that anyone reviewed it.

### Your own Jev access and budget

The released wrapper reads `TYPESAFE_API_KEY` from its process environment. It never reads another project's stored key. Supply credentials privately; never commit them, echo them into a recording or paste them into public logs.

Set `L4D2_JEV_CAP_USD` to your own authorized amount. It defaults to zero, so a fresh copy has no inherited spending authorization. The local ledger reserves uncertain requests, settles confirmed usage once, and has a single-owner lock. Its configured price is the historical run assumption, not a current pricing promise or provider-side hard billing limit. Check your provider's terms and pricing before enabling paid calls.

For a verified, paused and already recording local session, `run_jev.ps1 -Mode general -GamePid <verified PID> -Seconds <bound> -Resume` starts a bounded run. `-Resume` is only for a genuinely paused game. `smoke` and `evaluate` modes also make paid calls. Unit tests do not.

Planner directives and screenshot observations are ordinary public task instructions, subject to map, position and freshness checks. There is no included service that reproduces the private Astra conversation automatically.

### Optional media tools

Use a separate environment for the versions in `requirements-media.txt`; the media environment used a different NumPy pin from the controller. The scripts expect your own private source files, manifests and review decisions. They cannot rebuild the original film without its private inputs.

`project_paths.py` supports `L4D2_FFMPEG`, `L4D2_OCR_MODELS`, `L4D2_WHISPER_MODEL` and `L4D2_PAUSE_REFERENCE`. OCR expects the configured PP-OCRv4 detector/recognizer files; ASR expects an already available local Whisper model directory. No model download is performed by the review scripts. Pause detection needs your own reference image.

`render_story_export.py` assembles exact source intervals with FFmpeg concat-segment selection, encodes one full-size output, strips source metadata and checks free space while encoding. Its smoke mode exercises every segment before a long render. Completed encoding is **not** approval to publish. Automated OCR, transcripts and contact sheets can miss information and must not be described as a complete human watch/listen.

## Release boundaries and license

Only original Python, Squirrel, PowerShell, configuration and documentation are released. The MIT license applies to that original code. Left 4 Dead 2, Valve assets, third-party programs and models retain their own ownership and licenses and are not included. Runtime state, recordings, saves, personal data, credentials, downloaded reference documents and compiled addons are excluded by an explicit release allowlist, with `.gitignore` as an additional guard.

Portability changes in the release are limited to configurable dependency/game paths, a credential-free launch wrapper and an explicit zero-default Jev budget. Campaign logic is the original final controller. The fast-player/slow-planner architecture was inspired by the publicly shared [Minecraft agent project](https://github.com/rmalde/minecraft-agent); this release contains the original L4D2 implementation.
