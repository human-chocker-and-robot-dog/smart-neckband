package com.smartneckband.companion.data

/** A fresh launcher task/process starts again; rotation/background return preserves Stop. */
class LaunchPolicy {
    private var handled = false
    private var task: Int? = null
    fun attachTask(id: Int) { if (task != id) { task = id; handled = false } }
    fun finishTask(id: Int) { if (task == id) { task = null; handled = false } }
    fun autoStart(hasDevice: Boolean, hasPermissions: Boolean): Boolean {
        if (handled || !hasDevice || !hasPermissions) return false
        handled = true
        return true
    }
    fun stop() { handled = true }
    fun manualStart() { handled = true }
}
