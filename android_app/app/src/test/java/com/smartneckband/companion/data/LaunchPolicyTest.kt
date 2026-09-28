package com.smartneckband.companion.data

import org.junit.Assert.*
import org.junit.Test

class LaunchPolicyTest {
    @Test fun waitForSetupThenStartOnlyOnce() {
        val policy = LaunchPolicy()
        assertFalse(policy.autoStart(false, true))
        assertFalse(policy.autoStart(true, false))
        assertTrue(policy.autoStart(true, true))
        assertFalse(policy.autoStart(true, true))
    }
    @Test fun stopSurvivesRotationAndBackgroundButNotFreshProcess() {
        val policy = LaunchPolicy()
        policy.attachTask(10)
        policy.stop()
        policy.attachTask(10)
        repeat(3) { assertFalse(policy.autoStart(true, true)) }
        policy.attachTask(11)
        assertTrue(policy.autoStart(true, true))
        policy.stop()
        policy.finishTask(11)
        policy.attachTask(11)
        assertTrue(policy.autoStart(true, true))
        assertTrue(LaunchPolicy().autoStart(true, true))
    }
}
