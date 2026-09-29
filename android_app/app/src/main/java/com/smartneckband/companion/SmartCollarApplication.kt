package com.smartneckband.companion

import android.app.Application
import com.smartneckband.companion.data.CollarRepository
import com.smartneckband.companion.data.LaunchPolicy
import com.smartneckband.companion.data.DiagnosticCapture
import com.smartneckband.companion.data.AiSettingsStore
import com.smartneckband.companion.data.InsightEngine
import kotlinx.coroutines.flow.MutableStateFlow

class SmartCollarApplication : Application() {
    val repository = CollarRepository()
    val launchPolicy = LaunchPolicy()
    val preferences by lazy { getSharedPreferences("companion", MODE_PRIVATE) }
    val ecgDemo by lazy { MutableStateFlow(preferences.getBoolean("demo_ecg", false)) }
    val insightDemo by lazy { MutableStateFlow(preferences.getBoolean("demo_insight", false)) }
    val diagnostics = DiagnosticCapture()
    val aiSettings by lazy { AiSettingsStore(this) }
    val insightEngine by lazy { InsightEngine(preferences, repository, aiSettings) }

    fun setEcgDemo(enabled: Boolean) {
        preferences.edit().putBoolean("demo_ecg", enabled).apply()
        ecgDemo.value = enabled
        if (enabled) insightEngine.cancelActive("演示模式已开启，AI 请求已暂停。")
    }
    fun setInsightDemo(enabled: Boolean) {
        preferences.edit().putBoolean("demo_insight", enabled).apply()
        insightDemo.value = enabled
        if (enabled) insightEngine.cancelActive("演示模式已开启，AI 请求已暂停。")
    }
}
