package com.smartneckband.companion

import android.app.Application
import com.smartneckband.companion.data.CollarRepository
import com.smartneckband.companion.data.LaunchPolicy

class SmartCollarApplication : Application() {
    val repository = CollarRepository()
    val launchPolicy = LaunchPolicy()
    val preferences by lazy { getSharedPreferences("companion", MODE_PRIVATE) }
}
