package com.smartneckband.companion

import android.app.Application
import com.smartneckband.companion.data.CollarRepository
import com.smartneckband.companion.data.LaunchPolicy
import com.smartneckband.companion.data.DiagnosticCapture
import kotlinx.coroutines.flow.MutableStateFlow

class SmartCollarApplication : Application() {
    val repository = CollarRepository()
    val launchPolicy = LaunchPolicy()
    val preferences by lazy { getSharedPreferences("companion", MODE_PRIVATE) }
    val ecgDemo by lazy { MutableStateFlow(preferences.getBoolean("demo_ecg", false)) }
    val insightDemo by lazy { MutableStateFlow(preferences.getBoolean("demo_insight", false)) }
    val diagnostics = DiagnosticCapture()

    fun setEcgDemo(enabled: Boolean) {
        preferences.edit().putBoolean("demo_ecg", enabled).apply()
        ecgDemo.value = enabled
    }
    fun setInsightDemo(enabled: Boolean) {
        preferences.edit().putBoolean("demo_insight", enabled).apply()
        insightDemo.value = enabled
    }
}
