plugins {
    id("com.chaquo.python")
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "com.smartneckband.companion"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.smartneckband.companion"
        minSdk = 30
        targetSdk = 36
        versionCode = 2
        versionName = "0.1.1"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
        vectorDrawables { useSupportLibrary = true }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlin {
        compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    packaging.resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    testOptions.unitTests.all { it.systemProperty("repository.root", rootProject.projectDir.parent) }
}

// Package the existing PC implementation directly; no fork of the ECG algorithm.
val sharedPython = tasks.register<Sync>("stageSharedPython") {
    from("../../pc_app/src") {
        include("smart_neckband/__init__.py", "smart_neckband/protocol.py",
            "smart_neckband/buffers.py", "smart_neckband/source_coordinator.py",
            "smart_neckband/analysis.py", "smart_neckband/health_motion.py")
    }
    into(layout.buildDirectory.dir("generated/sharedPython"))
}
chaquopy {
    defaultConfig {
        version = "3.10"
        providers.gradleProperty("buildPython").orNull?.let { buildPython(it) }
        pip { install("-r", "requirements-android.txt") }
    }
    sourceSets.getByName("main") { srcDir(layout.buildDirectory.dir("generated/sharedPython")) }
}
tasks.configureEach {
    if (name.contains("PythonSources")) dependsOn(sharedPython)
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.core.splashscreen)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.ui)
    implementation(libs.androidx.ui.graphics)
    implementation(libs.androidx.ui.tooling.preview)
    implementation(libs.androidx.material3)
    implementation(libs.androidx.material.icons.extended)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.kotlinx.coroutines.android)
    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation("org.json:json:20250517")
    androidTestImplementation("androidx.test:runner:1.7.0")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
