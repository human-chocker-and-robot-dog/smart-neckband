# Copy this file to config/local.ps1 and edit local-only values.

$ProjectSerialPort = "COM7"
$ProjectFlashBaud = 460800
$ExpectedIdfVersion = "v6.0.2"
# Keep "esp32" while using the legacy board, or change to "esp32c3" after
# confirming the exact SuperMini clone. Every project.ps1 action also accepts
# an explicit -Target esp32 or -Target esp32c3 override.
$ExpectedTarget = "esp32"
$ExpectedFlashSize = "4MB"

# Exact installed Codex Agent Skill name. This is documentation for the agent;
# PowerShell itself does not invoke the skill.
$PowerShellSkillName = "powershell"
