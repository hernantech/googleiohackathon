# AGENTS.md — picking up `quest/`

Operating instructions for an AI agent inheriting this directory.

If you are a human, you can ignore this and read `quest/README.md` directly —
it's the full spec.

---

## TL;DR

1. **The baseline already works.** A panel renders in MR passthrough on
   a Quest 3/3S. Confirm this before changing anything, and re-confirm
   after.
2. **Read `quest/README.md` end-to-end before editing.** It documents
   coordinate conventions and load-bearing config that took a multi-day
   debug session to nail down; you will reintroduce known bugs if you
   guess.
3. **`gradlew :app:assembleDebug -x :app:export` is the only build
   command.** Never run `:app:export` (requires the Meta Spatial Editor
   desktop app, which is not installed; the task will fail).
4. **Standard adb capture tools do not see VR content.** Use
   `npx -y @meta-quest/hzdb capture screenshot --method metacam`. The
   headset has to be on someone's face — proximity sensor gates display.
5. **Ask the user before deleting or restructuring** any of the existing
   files. They are the *minimum* set that produces a visible panel; the
   omissions are deliberate.

---

## Environment you can assume

| | |
|---|---|
| Working tree | `/Users/alexhernandez/work/galois/googleio-quest` |
| Branch | `hernantech/quest` (off `origin/main`) |
| Working dir | `quest/` (always run gradle from here) |
| Android SDK | `~/Library/Android/sdk` (set in `local.properties`) |
| ANDROID_HOME | Often not exported; either export it or rely on `local.properties` |
| `adb` device | Quest 3 or 3S on USB, dev mode on |
| `hzdb` | Installable via `npx -y @meta-quest/hzdb …` (no global install) |
| Spatial SDK | 0.13.0 from Maven Central |
| Kotlin / AGP | 2.1.0 / 8.5.0 — coupled to Spatial SDK 0.13, don't bump alone |

---

## Regression check (run this before and after every change)

```bash
cd /Users/alexhernandez/work/galois/googleio-quest/quest
export ANDROID_HOME=$HOME/Library/Android/sdk   # if not already
./gradlew :app:assembleDebug -x :app:export

# Install + launch on the Quest
adb shell am force-stop com.meta.spatial.samples.mixedrealitytemplate
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n com.meta.spatial.samples.mixedrealitytemplate/.ImmersiveActivity

# Wait, then capture (only valid while the headset is being worn)
sleep 5
npx -y @meta-quest/hzdb capture screenshot --method metacam \
  --output /tmp/quest_check_$(date +%s).jpg
```

**Pass criteria.** In the captured JPEG you should see:
- Room passthrough as the background (couches, ceiling, etc.)
- A rectangular white panel ~1 m in front of the wearer at eye level
- Text "Choose option to play video." centered on it

If the wearer's gaze was elsewhere when the screenshot was taken, the
panel may be off-screen; check logs first:

```bash
adb logcat -d --pid=$(adb shell pidof com.meta.spatial.samples.mixedrealitytemplate) \
  | grep -iE 'glxf|Creating panel|onSceneReady|exception|fatal'
```

You want at least one `ISDK: Creating panel <n>` line per Composition.glxf
node, no `AndroidRuntime: FATAL EXCEPTION`, and a `MRT: onSceneReady`
log line.

---

## What you may do without asking

- Add **new** files (Compose UI, Kotlin classes, additional panels, new
  resources) as long as the regression check still passes.
- Add components to `app/src/main/components/components.xml` for new
  spatial components.
- Author additional nodes in `Composition.glxf`.
- Add dependencies to `gradle/libs.versions.toml` and reference them in
  `app/build.gradle.kts`, **provided** they don't pull in a different
  Kotlin/Compose major version.
- Refactor `OptionsPanelLayout.kt` (the existing unused options panel)
  freely — it's not load-bearing.

## What requires user confirmation first

- Changes to `AndroidManifest.xml` (especially feature flags,
  permissions, or the launcher intent filter).
- Changes to `ImmersiveActivity.registerFeatures()`,
  `onCreate()`, or `onSceneReady()` — every line there is load-bearing
  (see README § 6).
- Bumping any version in `libs.versions.toml`.
- Removing existing files.
- Pushing to remote, opening a PR, force-pushing, amending past commits.
- Running anything that touches the Quest's system state beyond
  installing/launching/killing the app (e.g., `pm grant`, `settings put`,
  `appops set`).

## What you must never do

- Run `./gradlew :app:export` (no Meta Spatial Editor CLI; it will fail).
- Commit `local.properties` or `app/build/` (both gitignored — verify
  with `git status` before committing).
- Use `adb shell screencap` / `screenrecord` / `scrcpy` to claim a UI
  state — they don't see immersive content. Use `hzdb metacam`.
- Skip pre-commit hooks (`--no-verify`) or signing flags.
- Add `// removed` / `// TODO: re-add` comments for code you delete.
  Delete cleanly.

---

## Coordinate convention cheat-sheet (memorize)

Misreading any of these silently puts a panel behind the wearer, upside
down, or both.

- **Spatial SDK forward is `+Z`** (`Vector3.Forward == (0, 0, 1)`).
  Raw OpenXR is `-Z` forward. The SDK inverts it.
- **`Quaternion(w, x, y, z)`** in Kotlin. `w` first. Identity is
  `Quaternion(1f, 0f, 0f, 0f)`, **not** `Quaternion(0f, 0f, 0f, 1f)`
  (which is a 180° Z-roll).
- **3-arg `Quaternion(pitch, yaw, roll)`** is degrees, not radians.
- **GLXF / glTF quaternion in JSON is `[x, y, z, w]`** — opposite order
  from the Kotlin constructor. `[0, 1, 0, 0]` JSON = 180° around Y.
- **`setReferenceSpace(LOCAL_FLOOR)` ⇒ y=0 is the floor**, so eye-level
  for a seated wearer is roughly `y = 1.2…1.5`.
- **Current view origin is `setViewOrigin(0, 0, 2, 180)`** — wearer at
  `(0, 0, 2)` yawed 180°. Combined with +Z forward, "in front of the
  wearer" means `z < 2`. The Composition.glxf places the panel at
  `z = 1.0` ⇒ 1 m in front.
- **Compose +Y-down vs world +Y-up** — if a Compose panel renders
  upside down on the quad, apply `Quaternion(180f, 0f, 0f)` to the
  panel's transform.

---

## Where to look when something breaks

1. **Did the build fail?** — read full gradle output, not just the
   summary. The first `e: ` or `error:` line is usually the root cause.
2. **Did the build succeed but the app silently does nothing visible?**
   - Run the `PTApiClients` log check:
     ```bash
     adb logcat -d | grep PTApiClients | tail -3
     ```
     `rec=0, proj=0` after onSceneReady = the projection layer never
     got submitted. Usual causes: `VRFeature` removed,
     `enablePassthrough(true)` removed, GLXF inflate threw silently.
   - Run the GLXF inflate check (`ISDK: Creating panel` line above).
3. **Did the metacam screenshot return < 20 KB?** — headset is not being
   worn. Wakefulness check:
   ```bash
   adb shell dumpsys power | grep mWakefulness
   ```
   Anything other than `Awake` ⇒ ask the wearer to put it on.
4. **General confusion / silent failure modes** — read
   `../../hackathon/BUILD_LEARNINGS.md`. Every failure mode found during
   this baseline's construction is documented there with root cause and
   fix.

---

## Communication norms

- When you propose a change, lead with **why** (what's broken or
  missing) before **what** (the diff).
- When you finish a change, **don't** declare "complete" without
  running the regression check end-to-end on a real headset with a
  screenshot to confirm. Type-checking and `assembleDebug` succeeding
  is necessary but insufficient — they do not detect the silent
  no-projection-layer failure mode.
- If the user gave you a high-level task ("port Forge logic in",
  "add a HUD"), restate it in 1–2 sentences before executing so they
  can correct misunderstandings cheaply.
- Use absolute paths and `git -C <path>` in commands; the shell's
  working directory typically isn't the worktree root.

---

## Cross-references

- `quest/README.md` — full handoff spec (build, data flow, file map,
  coordinate conventions, extension recipes, debugging)
- `../../hackathon/BUILD_LEARNINGS.md` — every failure mode and fix
  encountered while building this baseline (22 distinct fixes)
- `../../hackathon/forge_quest2/` — the dev sandbox copy of this same
  template; safe to break, not committed to this repo
- Meta Spatial SDK reference:
  https://developers.meta.com/horizon/reference/spatial-sdk/latest/
