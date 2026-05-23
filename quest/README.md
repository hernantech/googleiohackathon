# quest/ — Meta Spatial SDK MR baseline

A minimum-viable Quest 3 / 3S app that renders a panel in mixed-reality
passthrough. This is the foundation we'll build the Forge MR UI on top of.

It is the **MixedRealityTemplate** from `meta-quest/Meta-Spatial-SDK-Samples`,
stripped to the smallest set of files that still produces a visible panel,
plus a hand-authored `Composition.glxf` so the build does not depend on the
Meta Spatial Editor desktop CLI we don't have.

If you change anything in here, read the **Load-bearing config** section
first — there are several non-obvious settings whose interactions took a
full session to figure out.

---

## 1. Prerequisites

| Tool | Version | Notes |
|---|---|---|
| JDK | 17+ | AGP 8.5 requires it; we use 17/21 |
| Android SDK | platforms 34+ | `compileSdk = 34`, `targetSdk = 34` |
| `adb` | platform-tools | Talks to the Quest over USB |
| Quest 3 / 3S | Developer Mode on | Pair via Meta Quest Developer Hub or `oh my god this is a chore` |
| `npx @meta-quest/hzdb` | optional | Only way to screenshot in-MR content |

You do **not** need:
- Meta Spatial Editor desktop app
- Unity / Unreal
- A Meta Horizon account beyond what's needed to enable dev mode

### local.properties

Create `quest/local.properties` (gitignored):

```properties
sdk.dir=/Users/<you>/Library/Android/sdk
```

Or export `ANDROID_HOME=...` in your shell.

---

## 2. Build, install, run

From `quest/`:

```bash
# Build the APK (skip Meta Spatial Editor export task — we don't use it)
./gradlew :app:assembleDebug -x :app:export

# Install on the connected Quest
adb install -r app/build/outputs/apk/debug/app-debug.apk

# Force-stop if it was already running, then launch
adb shell am force-stop com.meta.spatial.samples.mixedrealitytemplate
adb shell am start -n com.meta.spatial.samples.mixedrealitytemplate/.ImmersiveActivity
```

To verify the panel is actually rendering (must be wearing the headset —
the proximity sensor gates display rendering):

```bash
# Optional: capture a real screenshot of what the wearer sees.
# screencap / screenrecord / scrcpy do NOT capture immersive content.
npx -y @meta-quest/hzdb capture screenshot --method metacam \
  --output /tmp/quest_$(date +%s).jpg
```

You should see a white rectangular panel ~1m in front saying
"Choose option to play video." That's the `ui_example` layout XML panel.

---

## 3. File map

```
quest/
├── build.gradle.kts                 — root: just registers plugins (no source)
├── settings.gradle.kts              — pluginManagement + mavenCentral + google
├── gradle.properties                — JVM args, AndroidX flags
├── gradle/
│   ├── libs.versions.toml           — single source of truth for versions
│   └── wrapper/                     — gradle 9.4.1 wrapper
├── gradlew, gradlew.bat
└── app/
    ├── build.gradle.kts             — applies plugins, deps, spatial { } block
    ├── proguard-rules.pro           — empty (R8 not enabled in debug)
    └── src/main/
        ├── AndroidManifest.xml      — VR + passthrough + handtracking features
        ├── components/components.xml — empty schema, required by the spatial plugin
        ├── assets/scenes/
        │   └── Composition.glxf     — hand-authored, see § 5
        ├── java/com/meta/spatial/samples/mixedrealitytemplate/
        │   ├── ImmersiveActivity.kt  — the entire app, ~150 LoC. See § 4.
        │   └── OptionsPanelLayout.kt — Compose UI for the (currently unused)
        │                              options panel, kept because
        │                              ImmersiveActivity registers it.
        └── res/
            ├── drawable/layout_bg.xml — rounded white background for ui_example
            ├── layout/ui_example.xml  — TextView + WebView inside a LinearLayout.
            │                            The visible panel.
            └── values/
                ├── ids.xml             — R.id.ui_example, R.id.options_panel
                ├── styles.xml          — PanelAppThemeTransparent (used by
                │                          OptionsPanel only)
                ├── constants.xml       — empty
                └── strings.xml         — empty
```

What's **not** here (deliberately, vs. the upstream sample):

| Removed | Why it's safe to remove |
|---|---|
| `app/scenes/Main.metaspatial` + sibling `*.metaspatialobj` | Meta Spatial Editor sources; we hand-author `Composition.glxf` instead |
| `assets/environment.env` | Only used by `scene.updateIBLEnvironment(...)`, which we comment out |
| `res/drawable/skydome.jpg` | Used by the Meta-Editor-authored scene we deleted |
| `assets/LICENSE.md` | Template metadata, not referenced by code |
| `AGENTS.md`, `README.md` (sample's) | Template docs replaced by this file |

---

## 4. Data flow — how a panel ends up on your retina

When `ImmersiveActivity` boots, four things have to line up. If any is
missing or wrong, the panel will silently fail to render (no exception,
no crash — just nothing visible) and `PTApiClients` will log `proj=0`.

```
              ┌──────────────────────────────────────────────┐
              │  AndroidManifest.xml                         │
              │    com.oculus.feature.PASSTHROUGH (required=false)
              │    com.oculus.intent.category.VR launcher    │
              │    com.oculus.vr.focusaware                  │
              └────────────────────┬─────────────────────────┘
                                   │
                                   ▼
   ┌───────────────────────────────────────────────────────────┐
   │  ImmersiveActivity.registerFeatures()                     │
   │    VRFeature(this)         ← initializes OpenXR pipeline  │
   │    ComposeFeature()        ← attaches lifecycle to panels │
   │    IsdkFeature(this, ...)  ← interaction toolkit          │
   └────────────────────┬──────────────────────────────────────┘
                        │
                        ▼
        ┌───────────────────────────────────────┐
        │  ImmersiveActivity.onCreate()         │
        │    scene.enablePassthrough(true)      │
        │    loadGLXF()  ← inflates             │
        │                  Composition.glxf     │
        └───────────────────┬───────────────────┘
                            │
                            ▼
        ┌─────────────────────────────────────────────────┐
        │  assets/scenes/Composition.glxf                 │
        │    nodes[0]:                                    │
        │      translation = [0, 1.5, 1.0]               │
        │      rotation    = [0, 1, 0, 0]  (glXF x,y,z,w)│
        │      components:                                │
        │        com.meta.spatial.toolkit.Panel          │
        │          panel = "@id/ui_example"  ← string ID │
        │        com.meta.spatial.toolkit.PanelDimensions│
        │          dimensions = [1.0, 0.75]              │
        └─────────────────────────────┬───────────────────┘
                                      │
                  resolves "@id/ui_example" against …
                                      │
                                      ▼
   ┌─────────────────────────────────────────────────────────┐
   │  ImmersiveActivity.registerPanels()                     │
   │    LayoutXMLPanelRegistration(                          │
   │      R.id.ui_example,                                   │
   │      layoutIdCreator = { _ -> R.layout.ui_example },    │
   │      settingsCreator = { _ -> UIPanelSettings() },      │
   │      panelSetupWithRootView = { rootView, _, _ -> ... } │
   │    )                                                    │
   └─────────────────────────────┬───────────────────────────┘
                                 │
                                 ▼
                  res/layout/ui_example.xml inflated,
                  rendered to a texture, drawn on a
                  3D quad in world space.
                                 │
                                 ▼
   ┌─────────────────────────────────────────────────────────┐
   │  ImmersiveActivity.onSceneReady()                       │
   │    scene.setReferenceSpace(LOCAL_FLOOR)                 │
   │      → y=0 means the floor                              │
   │    scene.enableHolePunching(true)                       │
   │      → MR composites panels over the passthrough        │
   │    scene.setViewOrigin(0, 0, 2, 180)                    │
   │      → wearer at world (0,0,2), yawed 180°. With +Z     │
   │        forward, yawing 180° = facing -Z. Panel at z=1   │
   │        is therefore 1m in front of the wearer.          │
   └─────────────────────────────────────────────────────────┘
```

---

## 5. Composition.glxf — the only authored scene file

The whole scene is one JSON document. Spec is glTF 2.0 with a Meta
`extras.meta_spatial` namespace for component data. The template's
upstream tooling generates this from a `Main.metaspatial` file via the
Meta Spatial Editor desktop CLI, but the format is documented and
small enough to write by hand.

Current contents (single panel):

```json
{
  "nodes": [{
    "name": "Panel",
    "rotation":    [0, 1, 0, 0],
    "translation": [0, 1.5, 1.0],
    "extras": {
      "meta_spatial": {
        "version": 1,
        "components": {
          "com.meta.spatial.toolkit.Panel": {
            "panel": {
              "keyString": "panel",
              "type": "String",
              "value": "@id/ui_example"
            }
          },
          "com.meta.spatial.toolkit.PanelDimensions": {
            "dimensions": {
              "keyString": "dimensions",
              "type": "Vector2",
              "value": [1.0, 0.75]
            }
          }
        }
      }
    }
  }],
  "scenes": [{ "name": "", "nodes": [0] }],
  "scene": 0,
  "asset": {
    "experience": false, "copyright": "", "version": "2.0",
    "generator": "", "minVersion": "2.0"
  }
}
```

Two non-obvious things:

1. **Quaternion order in GLXF is `[x, y, z, w]`** (glTF convention).
   In Spatial SDK Kotlin code, `Quaternion(w, x, y, z)` is **`w` first**.
   So `[0, 1, 0, 0]` in JSON = `Quaternion(0, 0, 1, 0)` in Kotlin = 180°
   around the Y axis. Identity in JSON is `[0, 0, 0, 1]`, identity in
   Kotlin is `Quaternion(1, 0, 0, 0)`.

2. **The panel-ID string is `@id/<name>`**, not `@layout/<name>`.
   It must match an `<item type="id" name="..."/>` in `res/values/ids.xml`,
   and the same `R.id.*` must appear in `registerPanels()` as the first
   argument to `LayoutXMLPanelRegistration` / `PanelRegistration`.
   The layout the panel renders comes from `layoutIdCreator` returning
   `R.layout.<something>`, which is a separate lookup.

---

## 6. Load-bearing config (do not touch without thinking)

These are the lines that took multiple debug sessions to get right.
Each one's removal/change can produce silent rendering failure.

### `AndroidManifest.xml`

```xml
<uses-feature android:name="com.oculus.feature.PASSTHROUGH"
              android:required="false" />
<meta-data android:name="com.oculus.vr.focusaware" android:value="true" />
```

`required="false"` for passthrough so the app launches on Quest 2 (which
will fall back to VR). `focusaware="true"` so the app keeps rendering
when the system menu overlays.

### `ImmersiveActivity.registerFeatures()`

```kotlin
VRFeature(this),       // ← initializes the OpenXR render pipeline.
                       //    Without this, the app boots, panels register,
                       //    no crash — but `PTApiClients` logs proj=0
                       //    forever and nothing is visible.
ComposeFeature(),      // ← provides ViewTreeLifecycleOwner et al. so
                       //    ComposeView inside panels doesn't crash.
IsdkFeature(this, spatial, systemManager),  // interaction toolkit
```

### `ImmersiveActivity.onCreate()`

```kotlin
scene.enablePassthrough(true)   // opt in — manifest feature only
                                // declares capability, not activation.
loadGLXF()                      // panel comes from Composition.glxf
```

### `ImmersiveActivity.onSceneReady()`

```kotlin
scene.setReferenceSpace(ReferenceSpace.LOCAL_FLOOR)
scene.enableHolePunching(true)         // tells the compositor to cut
                                       // holes through the passthrough
                                       // layer where our panels are
scene.setViewOrigin(0f, 0f, 2f, 180f)  // wearer at (0,0,2), yaw 180°
```

The `(0, 0, 2, 180)` view origin is paired with content at z<2 so the
wearer naturally faces the panel. If you put the panel at z=-1.7 instead
(as the upstream sample does), it's 3.7m away — too far for "in front of
you" framing.

### `app/build.gradle.kts`

The Spatial SDK 0.13.0 deps pull in Kotlin 2.1 stdlib transitively, so
`kotlin = "2.1.0"` and `agp = "8.5.0"` in `libs.versions.toml` are
coupled to that. Don't downgrade Kotlin without downgrading the SDK.

---

## 7. Coordinate conventions cheat-sheet

| | Spatial SDK | OpenXR (raw) | glTF (GLXF) |
|---|---|---|---|
| Forward axis | +Z | −Z | (no forward, just transforms) |
| Up axis | +Y | +Y | +Y |
| Quaternion order | `(w, x, y, z)` | `(x, y, z, w)` | `[x, y, z, w]` |
| Identity quat | `Quaternion(1, 0, 0, 0)` | `(0, 0, 0, 1)` | `[0, 0, 0, 1]` |
| 3-arg Euler | `Quaternion(pitchDeg, yawDeg, rollDeg)` | n/a | n/a |
| Vector3.Forward | `(0, 0, 1)` | `(0, 0, -1)` | n/a |

Misinterpreting any of these silently puts your panel behind you,
upside down, or both.

---

## 8. How to extend

### Add a second panel

1. In `res/values/ids.xml`:
   ```xml
   <item type="id" name="my_new_panel" />
   ```
2. In `res/layout/`, add `my_new_panel.xml` for an XML layout, **or**
   skip step 2 entirely and use Compose (see next subsection).
3. In `ImmersiveActivity.registerPanels()`, append:
   ```kotlin
   LayoutXMLPanelRegistration(
       R.id.my_new_panel,
       layoutIdCreator = { _ -> R.layout.my_new_panel },
       settingsCreator = { _ -> UIPanelSettings() },
       panelSetupWithRootView = { rootView, _, _ -> /* wire up */ },
   ),
   ```
4. In `Composition.glxf`, add a second node with
   `"panel": { "value": "@id/my_new_panel", ... }` and your chosen
   transform.

### Use Compose instead of an XML layout

```kotlin
ComposeViewPanelRegistration(
    R.id.my_compose_panel,
    composeViewCreator = { _, ctx ->
        ComposeView(ctx).apply { setContent { MyPanelContent() } }
    },
    settingsCreator = {
        UIPanelSettings(
            shape  = QuadShapeOptions(width = 0.6f, height = 0.4f),
            style  = PanelStyleOptions(themeResourceId = R.style.PanelAppThemeTransparent),
            display = DpPerMeterDisplayOptions(),
        )
    },
)
```

If your Compose UI looks upside down: apply `Quaternion(180f, 0f, 0f)`
(180° pitch) to the panel's transform. Compose draws +Y-down, the panel
quad is +Y-up; the 180° pitch flips it.

### Move a panel without re-authoring GLXF

Pull the panel entity by name in `onSceneReady()` and update its
`Transform`:

```kotlin
val panel = systemManager.findEntity { it.getComponent<NameComponent>()?.name == "Panel" }
panel?.setComponent(Transform(Pose(Vector3(0f, 1.4f, 0.8f), Quaternion(0f, 180f, 0f))))
```

---

## 9. Debugging recipes

### "Is the projection layer actually being submitted?"

```bash
adb logcat -d 2>&1 | grep PTApiClients | tail -3
```

You want to see something like:

```
PTApiClients: 1/2:com.meta.spatial.samples.mixedrealitytemplate,PT=1,...,LCnt=1/1(rec=1,proj=1,bg=0)
```

`proj=0` consistently = the SDK is not handing the compositor a panel
layer. Usual causes: `VRFeature` not registered, `enablePassthrough`
not called, GLXF failed to inflate.

(Note: `proj=0` was sometimes observed even when panels were visible.
It's a strong **negative** signal — `proj > 0` doesn't always appear
on every sample, but `proj=0` plus an empty headset view is the textbook
"silent failure".)

### "Did the GLXF inflate?"

```bash
adb logcat -d --pid=$(adb shell pidof com.meta.spatial.samples.mixedrealitytemplate) \
  | grep -iE 'glxf|panel|createSceneObject|ISDK: Creating panel'
```

You should see at least one `ISDK: Creating panel <n>` line per node.

### "Why is metacam returning a black frame?"

You're not wearing the headset (or the proximity sensor thinks you're
not). Quest gates display rendering on proximity, independent of the
`mWakefulness` power state:

```bash
adb shell dumpsys power | grep mWakefulness
# Asleep = device suspended, capture will be 0 bytes
# Awake but display off = wearing-state issue, capture will be black
```

Standard Android capture tools (`screencap`, `screenrecord`, `scrcpy`)
do **not** see immersive content on Quest. `hzdb capture screenshot
--method metacam` is the only reliable path.

### "The activity goes paused right after onSceneReady"

Same root cause: headset not being worn at launch time. Wear it, then
`adb shell am force-stop … && adb shell am start -n …/.ImmersiveActivity`
for a fresh `onCreate` → `onSceneReady` → `loadGLXF` cycle.

---

## 10. Known limitations / not-yet-implemented

- **No Meta Spatial Editor authoring loop.** All scene composition is
  done by hand-editing `Composition.glxf`. Acceptable for one panel,
  painful past five. Long-term: either get the CLI, or write a tiny
  Kotlin/Python helper that emits the JSON.
- **`:app:export` is permanently skipped** in the build invocation
  (`-x :app:export`). If the gradle plugin ever stops gracefully
  skipping that task and starts failing configuration when the source
  `.metaspatial` is missing, we'll need to remove the
  `spatial { scenes { exportItems { ... } } }` block from
  `app/build.gradle.kts` to drop the dependency entirely.
- **No persistent state, no networking, no audio/mic capture, no
  controller input handling.** This is the rendering shell only.
  Forge logic goes on top.
- **OptionsPanel (`OptionsPanelLayout.kt`) is registered but never
  shown.** It's kept because removing it means also touching
  `registerPanels()` and `ids.xml`, and we wanted the smallest
  diff from the known-good template. Delete it the first time you
  refactor `ImmersiveActivity`.

---

## 11. Cross-references

- Upstream sample: `meta-quest/Meta-Spatial-SDK-Samples`, directory
  `MixedRealityTemplate/`
- Field-tested gotchas log (every wrong turn that produced this
  baseline): `../../hackathon/BUILD_LEARNINGS.md`
- Spatial SDK Kotlin docs:
  https://developers.meta.com/horizon/reference/spatial-sdk/latest/
