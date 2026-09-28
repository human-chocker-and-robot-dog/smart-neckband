package com.smartneckband.companion

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import org.junit.Rule
import org.junit.Test

class NavigationTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    @Test fun todayShowsDataAndSettingsOwnsAcquisitionControls() {
        compose.onNodeWithText("心率").assertIsDisplayed()
        compose.onNodeWithText("停止采集").assertDoesNotExist()
        compose.onNodeWithText("扫描设备").assertDoesNotExist()
        compose.onNodeWithText("Settings").performClick()
        compose.onNodeWithText("扫描设备").assertIsDisplayed()
        compose.onNodeWithText("停止采集").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("AI Insight").performClick()
        compose.onNodeWithText("等待第一段有效记录").assertIsDisplayed()
    }
}
