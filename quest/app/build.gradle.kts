plugins {
  alias(libs.plugins.android.application)
  alias(libs.plugins.jetbrains.kotlin.android)
  alias(libs.plugins.meta.spatial.plugin)
  alias(libs.plugins.jetbrains.kotlin.plugin.compose)
  alias(libs.plugins.jetbrains.kotlin.plugin.serialization)
}

android {
  namespace = "com.meta.spatial.samples.mixedrealitytemplate"
  //noinspection GradleDependency
  compileSdk = 34

  defaultConfig {
    applicationId = "com.meta.spatial.samples.mixedrealitytemplate"
    minSdk = 34
    // HorizonOS is Android 14 (API level 34)
    //noinspection OldTargetApi,ExpiredTargetSdkVersion
    targetSdk = 34
    versionCode = 1
    versionName = "1.0"

    // Forge orchestrator (deployed, stub mode). Override per-build with
    // -PorchestratorUrl=ws://host:port/v2/chat if needed.
    buildConfigField(
        "String",
        "ORCHESTRATOR_URL",
        "\"${project.findProperty("orchestratorUrl") ?: "ws://20.230.188.247:8080/v2/chat"}\"",
    )
    buildConfigField(
        "String",
        "ORCHESTRATOR_SNAPSHOT_URL",
        "\"${project.findProperty("orchestratorSnapshotUrl") ?: "http://20.230.188.247:8080/v2/snapshot"}\"",
    )
    buildConfigField(
        "String",
        "ORCHESTRATOR_LIVE_URL",
        "\"${project.findProperty("orchestratorLiveUrl") ?: "ws://20.230.188.247:8080/v2/live"}\"",
    )

    testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

    // Update the ndkVersion to the right version for your app
    // ndkVersion = "27.0.12077973"
  }

  packaging { resources.excludes.add("META-INF/LICENSE") }

  lint {
    abortOnError = false
    checkReleaseBuilds = false
  }

  buildTypes {
    release {
      isMinifyEnabled = false
      proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
    }
  }
  buildFeatures {
    buildConfig = true
    compose = true
  }
  compileOptions {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
  }
  kotlinOptions { jvmTarget = "17" }
}

//noinspection UseTomlInstead
dependencies {
  implementation(libs.androidx.core.ktx)

  // Forge orchestrator client: WebSocket + JSON wire protocol (spec 00)
  implementation(libs.okhttp)
  implementation(libs.kotlinx.serialization.json)
  implementation(libs.kotlinx.coroutines.android)

  testImplementation(libs.junit)
  androidTestImplementation(libs.androidx.junit)
  androidTestImplementation(libs.androidx.espresso.core)

  // This project incorporates the Meta Spatial SDK, licensed under the Meta Platforms Technologies
  // SDK License Agreement available at https://developers.meta.com/horizon/licenses/oculussdk/
  // Meta Spatial SDK libs
  implementation(libs.meta.spatial.sdk.base)
  implementation(libs.meta.spatial.sdk.ovrmetrics)
  implementation(libs.meta.spatial.sdk.toolkit)
  implementation(libs.meta.spatial.sdk.vr)
  implementation(libs.meta.spatial.sdk.isdk)
  implementation(libs.meta.spatial.sdk.compose)
  implementation(libs.meta.spatial.sdk.castinputforward)
  implementation(libs.meta.spatial.sdk.hotreload)
  implementation(libs.meta.spatial.sdk.datamodelinspector)
  implementation(libs.meta.spatial.sdk.uiset)

  // Compose Dependencies
  implementation("androidx.compose.material3:material3")
  implementation(libs.androidx.lifecycle.runtime.ktx)
  implementation(libs.androidx.activity.compose)
  implementation(platform(libs.androidx.compose.bom))
  implementation(libs.androidx.ui)
  implementation(libs.androidx.ui.graphics)
  implementation(libs.androidx.ui.tooling.preview)
  androidTestImplementation(platform(libs.androidx.compose.bom))
  androidTestImplementation(libs.androidx.ui.test.junit4)
  debugImplementation(libs.androidx.ui.tooling)
  debugImplementation(libs.androidx.ui.test.manifest)
}

val projectDir = layout.projectDirectory
val sceneDirectory = projectDir.dir("scenes")

spatial {
  allowUsageDataCollection.set(true)
  scenes {
    // if you have installed Meta Spatial Editor somewhere else, update the file path.

    // cliPath.set("/Applications/Meta Spatial Editor.app/Contents/MacOS/CLI")

    exportItems {
      item {
        projectPath.set(sceneDirectory.file("Main.metaspatial"))
        outputPath.set(projectDir.dir("src/main/assets/scenes"))
      }
    }
    hotReload {
      appPackage.set("com.meta.spatial.samples.mixedrealitytemplate")
      appMainActivity.set(".ImmersiveActivity")
      assetsDir.set(File("src/main/assets"))
    }
  }
}
