package com.smartneckband.companion

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.graphics.asAndroidBitmap
import android.graphics.Bitmap
import androidx.test.platform.app.InstrumentationRegistry
import com.smartneckband.companion.ui.CompanionUi
import com.smartneckband.companion.data.*
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import java.io.File

class DemoModeTest {
    @get:Rule val compose = createComposeRule()

    @Test fun independentDemoSwitchesRestoreRealDataWithoutHardwareOrApi() {
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as SmartCollarApplication
        val savedEcg = app.ecgDemo.value
        val savedInsight = app.insightDemo.value
        val original = app.repository.snapshot.value
        try {
            app.setEcgDemo(false); app.setInsightDemo(false)
            compose.setContent { CompanionUi(app, "Test", emptyList(), false, null, {}, {}, {}, {}) }
            compose.onNodeWithText("Settings").performClick()
            compose.onNodeWithContentDescription("AI Insight 演示开关").performClick()
            assertTrue(app.insightDemo.value)
            assertFalse(app.ecgDemo.value)
            compose.onNodeWithText("AI Insight").performClick()
            compose.onNodeWithText("一段安静的时光").assertIsDisplayed()
            compose.onRoot().captureToImage().asAndroidBitmap().let { image ->
                File(app.cacheDir, "demo-insight.png").outputStream().use { image.compress(Bitmap.CompressFormat.PNG, 100, it) }
            }
            compose.onNodeWithText("Settings").performClick()
            compose.onNodeWithContentDescription("ECG 心率演示开关").performClick()
            compose.onNodeWithText("Today").performClick()
            compose.onNodeWithText("72").assertIsDisplayed()
            compose.onNodeWithText("演示模式 · 合成 ECG / 心率，非实际测量").assertIsDisplayed()
            // Freeze the animation clock for screenshot/idle assertions.
            compose.mainClock.autoAdvance = false
            compose.onRoot().captureToImage().asAndroidBitmap().let { image ->
                File(app.cacheDir, "demo-ecg.png").outputStream().use { image.compress(Bitmap.CompressFormat.PNG, 100, it) }
            }
            compose.mainClock.autoAdvance = true
            compose.onNodeWithText("Settings").performClick()
            compose.onNodeWithContentDescription("ECG 心率演示开关").performClick()
            assertFalse(app.ecgDemo.value)
            assertTrue(app.insightDemo.value)
            assertEquals(original, app.repository.snapshot.value)
            assertFalse(app.preferences.getBoolean("demo_ecg", true))
            compose.onNodeWithContentDescription("AI Insight 演示开关").performClick()
            assertFalse(app.insightDemo.value)
        } finally {
            app.setEcgDemo(savedEcg); app.setInsightDemo(savedInsight)
        }
    }
}
