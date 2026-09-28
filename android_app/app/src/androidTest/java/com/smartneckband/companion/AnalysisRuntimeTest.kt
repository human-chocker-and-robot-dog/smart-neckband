package com.smartneckband.companion

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class AnalysisRuntimeTest {
    @Test fun androidNativeLibrariesMatchDesktopSyntheticReplay() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        if (!Python.isStarted()) Python.start(AndroidPlatform(instrumentation.targetContext))
        val engine = Python.getInstance().getModule("collar_engine").callAttr("Engine")
        val fixture = JSONObject(instrumentation.context.assets.open("synthetic-ecg.json").bufferedReader().use { it.readText() })
        val batch = JSONObject().put("kind", "ecg").put("first", 0).put("timestamp", 0)
            .put("flags", 0).put("samples", fixture.getJSONArray("samples"))
        engine.callAttr("append", batch.toString())
        val result = JSONObject(engine.callAttr("analyze").toString())
        assertEquals(fixture.getDouble("bpm"), result.getDouble("bpm"), .05)
        assertEquals(fixture.getDouble("quality"), result.getDouble("quality"), .01)
        assertTrue(result.isNull("rmssd"))
        assertEquals(5000, result.getJSONObject("raw").getJSONArray("values").length())
        assertEquals(5000, result.getJSONObject("cleaned").getJSONArray("values").length())
    }
}
