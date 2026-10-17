"""FL Studio MCP tools."""

from fl_studio_mcp.tools.arrange import register_arrange_tools
from fl_studio_mcp.tools.audio import register_audio_tools
from fl_studio_mcp.tools.automation import register_automation_tools
from fl_studio_mcp.tools.browser import register_browser_tools
from fl_studio_mcp.tools.channel_setup import register_channel_setup_tools
from fl_studio_mcp.tools.channels import register_channel_tools
from fl_studio_mcp.tools.groups import register_group_tools
from fl_studio_mcp.tools.mix_check import register_mix_check_tools
from fl_studio_mcp.tools.mixer import register_mixer_tools
from fl_studio_mcp.tools.mixing import register_mixing_tools
from fl_studio_mcp.tools.music import register_music_tools
from fl_studio_mcp.tools.patterns import register_pattern_tools
from fl_studio_mcp.tools.piano_roll import register_piano_roll_tools
from fl_studio_mcp.tools.plugins import register_plugin_tools
from fl_studio_mcp.tools.project import register_project_tools
from fl_studio_mcp.tools.roles import register_roles_tools
from fl_studio_mcp.tools.screen import register_screen_tools
from fl_studio_mcp.tools.snapshots import register_snapshot_tools
from fl_studio_mcp.tools.styles import register_style_tools
from fl_studio_mcp.tools.transport import register_transport_tools
from fl_studio_mcp.tools.tuning import register_tuning_tools
from fl_studio_mcp.tools.vocals import register_vocal_tools

__all__ = [
    "register_transport_tools",
    "register_mixer_tools",
    "register_group_tools",
    "register_mixing_tools",
    "register_mix_check_tools",
    "register_channel_tools",
    "register_channel_setup_tools",
    "register_arrange_tools",
    "register_audio_tools",
    "register_automation_tools",
    "register_browser_tools",
    "register_plugin_tools",
    "register_piano_roll_tools",
    "register_music_tools",
    "register_project_tools",
    "register_pattern_tools",
    "register_roles_tools",
    "register_screen_tools",
    "register_snapshot_tools",
    "register_style_tools",
    "register_tuning_tools",
    "register_vocal_tools",
]
