package com.meta.spatial.samples.mixedrealitytemplate

import android.annotation.SuppressLint
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.View
import android.webkit.WebView
import android.widget.TextView
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.ui.platform.ComposeView
import androidx.core.net.toUri
import com.meta.spatial.samples.mixedrealitytemplate.forge.audio.MicCapture
import com.meta.spatial.samples.mixedrealitytemplate.forge.audio.SpeakerPlayer
import com.meta.spatial.samples.mixedrealitytemplate.forge.camera.PassthroughCapture
import com.meta.spatial.samples.mixedrealitytemplate.forge.net.LiveSocket
import com.meta.spatial.samples.mixedrealitytemplate.forge.net.OrchestratorSocket
import com.meta.spatial.samples.mixedrealitytemplate.forge.net.SnapshotUploader
import com.meta.spatial.samples.mixedrealitytemplate.forge.state.SessionState
import com.meta.spatial.samples.mixedrealitytemplate.forge.ui.ChatPanel
import com.meta.spatial.samples.mixedrealitytemplate.forge.ui.ConfirmationPanel
import com.meta.spatial.samples.mixedrealitytemplate.forge.ui.HudPanel
import com.meta.spatial.castinputforward.CastInputForwardFeature
import com.meta.spatial.compose.ComposeFeature
import com.meta.spatial.compose.ComposeViewPanelRegistration
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
import com.meta.spatial.core.Quaternion
import com.meta.spatial.core.SpatialFeature
import com.meta.spatial.core.SpatialSDKExperimentalAPI
import com.meta.spatial.core.Vector3
import com.meta.spatial.toolkit.Transform
import com.meta.spatial.toolkit.createPanelEntity
import com.meta.spatial.datamodelinspector.DataModelInspectorFeature
import com.meta.spatial.debugtools.HotReloadFeature
import com.meta.spatial.isdk.IsdkFeature
import com.meta.spatial.okhttp3.OkHttpAssetFetcher
import com.meta.spatial.ovrmetrics.OVRMetricsDataModel
import com.meta.spatial.ovrmetrics.OVRMetricsFeature
import com.meta.spatial.runtime.NetworkedAssetLoader
import com.meta.spatial.toolkit.AppSystemActivity
import com.meta.spatial.toolkit.DpPerMeterDisplayOptions
import com.meta.spatial.toolkit.LayoutXMLPanelRegistration
import com.meta.spatial.toolkit.PanelRegistration
import com.meta.spatial.toolkit.PanelStyleOptions
import com.meta.spatial.toolkit.QuadShapeOptions
import com.meta.spatial.toolkit.UIPanelSettings
import com.meta.spatial.vr.LocomotionSystem
import com.meta.spatial.vr.VRFeature
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

class ImmersiveActivity : AppSystemActivity() {
  private val activityScope = CoroutineScope(Dispatchers.Main)

  lateinit var textView: TextView
  lateinit var webView: WebView

  // Forge orchestrator session. Initialized in onCreate (before onSceneReady
  // creates the panels that reference it).
  private lateinit var session: SessionState

  override fun registerFeatures(): List<SpatialFeature> {
    val features =
        mutableListOf<SpatialFeature>(
            VRFeature(this),
            ComposeFeature(),
            IsdkFeature(this, spatial, systemManager),
        )
    if (BuildConfig.DEBUG) {
      features.add(CastInputForwardFeature(this))
      features.add(HotReloadFeature(this))
      features.add(OVRMetricsFeature(this, OVRMetricsDataModel() { numberOfMeshes() }))
      features.add(DataModelInspectorFeature(spatial, this.componentManager))
    }
    return features
  }

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    NetworkedAssetLoader.init(
        File(applicationContext.getCacheDir().canonicalPath),
        OkHttpAssetFetcher(),
    )

    // Enable MR mode
    systemManager.findSystem<LocomotionSystem>().enableLocomotion(false)
    scene.enablePassthrough(true)

    // Forge: connect to the orchestrator chat bus and project events into UI.
    val socket =
        OrchestratorSocket(baseUrl = BuildConfig.ORCHESTRATOR_URL, scope = activityScope)
    val capture = PassthroughCapture(applicationContext)
    val uploader = SnapshotUploader(BuildConfig.ORCHESTRATOR_SNAPSHOT_URL)
    val liveSocket =
        LiveSocket(
            liveUrl = BuildConfig.ORCHESTRATOR_LIVE_URL,
            sessionId = socket.sessionId, // same session so chat + live attach together
            scope = activityScope,
            mic = MicCapture(),
            speaker = SpeakerPlayer(),
            capture = capture,
            enableVideo = true,
        )
    session = SessionState(socket, activityScope, capture, uploader, liveSocket)
    // Request camera+mic perms LAZILY (on first 📷/🎙 tap), never at boot —
    // requesting at boot backgrounds the immersive activity to show the system
    // dialog and kills passthrough. The callback runs the legacy request.
    session.onRequestCameraPermission = {
      runOnUiThread { requestPermissions(MEDIA_PERMS, REQ_CAMERA) }
    }
    session.setMediaReady(hasMediaPerms())
    session.start()

    loadGLXF()
  }

  private fun hasMediaPerms(): Boolean =
      MEDIA_PERMS.all { checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }

  override fun onRequestPermissionsResult(
      requestCode: Int,
      permissions: Array<out String>,
      grantResults: IntArray,
  ) {
    super.onRequestPermissionsResult(requestCode, permissions, grantResults)
    if (requestCode == REQ_CAMERA && ::session.isInitialized) {
      session.setMediaReady(hasMediaPerms())
    }
  }

  override fun onSceneReady() {
    super.onSceneReady()

    // HybridSample setup that the MR template omits but appears load-bearing for the
    // projection layer to actually get submitted to the compositor.
    scene.setReferenceSpace(com.meta.spatial.runtime.ReferenceSpace.LOCAL_FLOOR)
    scene.enableHolePunching(true)

    scene.setLightingEnvironment(
        ambientColor = Vector3(0f),
        sunColor = Vector3(7.0f, 7.0f, 7.0f),
        sunDirection = -Vector3(1.0f, 3.0f, -2.0f),
        environmentIntensity = 0.3f,
    )
    // scene.updateIBLEnvironment("environment.env") — skipped, asset comes from scene export

    scene.setViewOrigin(0.0f, 0.0f, 2.0f, 180.0f)

    // Forge panels, centered in front of the wearer. The wearer is at (0,0,2)
    // facing -Z, so content at z<2 is in front; the 180°-about-Y rotation turns
    // each quad's face toward the wearer. Chat is the hero (center), HUD above,
    // confirmation below.
    val faceUser = Quaternion(0f, 0f, 1f, 0f) // 180° about Y, (w,x,y,z)
    spawnForgePanel(R.id.panel_forge_hud, Vector3(0f, 1.78f, 1.0f), faceUser)
    spawnForgePanel(R.id.panel_forge_chat, Vector3(0f, 1.35f, 1.0f), faceUser)
    spawnForgePanel(R.id.panel_forge_confirmation, Vector3(0f, 0.82f, 1.0f), faceUser)

    android.util.Log.i("MRT", "onSceneReady done — Forge panels mounted + GLXF panel")
  }

  private fun spawnForgePanel(panelId: Int, position: Vector3, rotation: Quaternion): Entity =
      Entity.createPanelEntity(panelId, Transform(Pose(position, rotation)))

  fun playVideo(webviewURI: String) {
    textView.visibility = View.GONE
    webView.visibility = View.VISIBLE
    val additionalHttpHeaders = mapOf("Referer" to "https://${packageName}")
    webView.loadUrl(webviewURI, additionalHttpHeaders)
  }

  @OptIn(SpatialSDKExperimentalAPI::class)
  override fun registerPanels(): List<PanelRegistration> {
    return listOf(
        // Registering light-weight Views panel
        LayoutXMLPanelRegistration(
            R.id.ui_example,
            layoutIdCreator = { _ -> R.layout.ui_example },
            settingsCreator = { _ -> UIPanelSettings() },
            panelSetupWithRootView = { rootView, _, _ ->
              webView =
                  rootView.findViewById<WebView>(R.id.web_view) ?: return@LayoutXMLPanelRegistration
              textView =
                  rootView.findViewById<TextView>(R.id.text_view)
                      ?: return@LayoutXMLPanelRegistration
              val webSettings = webView.settings
              @SuppressLint("SetJavaScriptEnabled")
              webSettings.javaScriptEnabled = true
              webSettings.mediaPlaybackRequiresUserGesture = false
            },
        ),
        // Registering a Compose panel
        ComposeViewPanelRegistration(
            R.id.options_panel,
            composeViewCreator = { _, context ->
              ComposeView(context).apply { setContent { OptionsPanel(::playVideo) } }
            },
            settingsCreator = {
              UIPanelSettings(
                  shape =
                      QuadShapeOptions(width = OPTIONS_PANEL_WIDTH, height = OPTIONS_PANEL_HEIGHT),
                  style = PanelStyleOptions(themeResourceId = R.style.PanelAppThemeTransparent),
                  display = DpPerMeterDisplayOptions(),
              )
            },
        ),
        // Forge MR panels (Compose) — chat console, HUD, confirmation.
        forgeComposePanel(R.id.panel_forge_hud, 0.62f, 0.20f) { HudPanel(session) },
        forgeComposePanel(R.id.panel_forge_chat, 0.90f, 0.62f) { ChatPanel(session) },
        forgeComposePanel(R.id.panel_forge_confirmation, 0.55f, 0.42f) {
          ConfirmationPanel(session)
        },
    )
  }

  /** A Compose panel sized in meters, wrapped in a dark Material theme. */
  @OptIn(SpatialSDKExperimentalAPI::class)
  private fun forgeComposePanel(
      panelId: Int,
      widthM: Float,
      heightM: Float,
      content: @androidx.compose.runtime.Composable () -> Unit,
  ): PanelRegistration =
      ComposeViewPanelRegistration(
          panelId,
          composeViewCreator = { _, context ->
            ComposeView(context).apply {
              setContent { MaterialTheme(colorScheme = darkColorScheme()) { content() } }
            }
          },
          settingsCreator = {
            UIPanelSettings(
                shape = QuadShapeOptions(width = widthM, height = heightM),
                style = PanelStyleOptions(themeResourceId = R.style.PanelAppThemeTransparent),
                display = DpPerMeterDisplayOptions(),
            )
          },
      )

  override fun onSpatialShutdown() {
    if (::session.isInitialized) session.stop()
    super.onSpatialShutdown()
  }

  private fun loadGLXF(): Job {
    return activityScope.launch {
      // Composition.glxf is intentionally empty now (the template's white panel
      // was removed); all Forge panels are placed programmatically in
      // onSceneReady. Guard the inflate so an empty scene can't abort boot.
      try {
        glXFManager.inflateGLXF(
            "apk:///scenes/Composition.glxf".toUri(),
            keyName = "example_key_name",
        )
      } catch (e: Exception) {
        android.util.Log.w("MRT", "GLXF inflate skipped: ${e.message}")
      }
    }
  }

  companion object {
    private const val REQ_CAMERA = 4201
    private val MEDIA_PERMS =
        arrayOf(
            android.Manifest.permission.CAMERA,
            PassthroughCapture.HEADSET_CAMERA,
            android.Manifest.permission.RECORD_AUDIO,
        )
  }
}
