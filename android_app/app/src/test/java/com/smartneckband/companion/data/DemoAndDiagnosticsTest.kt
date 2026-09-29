package com.smartneckband.companion.data

import com.smartneckband.companion.protocol.*
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class DemoAndDiagnosticsTest {
    @Test fun syntheticDisplayDoesNotReplaceRealRepository() {
        val repository = CollarRepository()
        val original = repository.snapshot.value
        val first = DemoData.snapshot()
        val next = DemoData.snapshot(.1)
        assertEquals(2500, first.rawEcg.values.size)
        assertTrue(first.rawEcg.values.all { it in 0f..4095f })
        assertNotEquals(first.rawEcg.values, next.rawEcg.values)
        assertEquals(72.0, first.heart.bpm!!, 0.0)
        assertEquals(original, repository.snapshot.value)
        assertTrue(repository.insights.value.isEmpty())
        assertTrue(DemoData.cards.all { it.source.contains("演示") && it.evidence != null && it.caveat != null })
    }

    @Test fun captureRetainsExactWireAndDecodedSamplesAndExpiresWhenIdle() {
        var now = 1000L
        val log = DiagnosticCapture(clockMs = { now }, retentionMs = 60_000)
        log.beginConnection()
        log.transport(byteArrayOf(0x53, 0x4e, 0, 0xff.toByte()))
        log.frame(V0Frame.Ecg(1, 100, 20, 500, 16, intArrayOf(0, 2048, 4095)), ParserStats(), true)
        val lines = log.snapshotLines(JSONObject()).map(::JSONObject)
        assertEquals("534e00ff", lines[2].getJSONObject("data").getString("hex"))
        assertEquals("[0,2048,4095]", lines[3].getJSONObject("data").getJSONArray("samples").toString())
        now += 60_001
        val expired = log.snapshotLines(JSONObject())
        assertEquals(1, expired.size)
        assertEquals(3, JSONObject(expired[0]).getInt("evicted_records"))
    }

    @Test fun captureHasByteBoundAndConnectionBoundaries() {
        val log = DiagnosticCapture(maxBytes = 800)
        log.beginConnection()
        val before = JSONObject(log.snapshotLines(JSONObject()).last()).getString("connection_id")
        repeat(30) { log.transport(ByteArray(20) { it.toByte() }) }
        log.beginConnection()
        val lines = log.snapshotLines(JSONObject()).map(::JSONObject)
        assertTrue(lines[0].getInt("evicted_records") > 0)
        assertNotEquals(before, lines.last().getString("connection_id"))
    }
}
