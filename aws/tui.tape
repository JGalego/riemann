# Records docs/tui.gif, docs/tui.mp4 (git-ignored) and a still, docs/tui.png, from the live dashboard: vhs aws/tui.tape (needs a running build).
# The TUI starts off-camera and warms up for 70 s so the rate, ETA and sparkline have data.
Output docs/tui.gif
Output docs/tui.mp4
Set Shell bash
Set FontSize 18
Set Width 1600
Set Height 820
Set Padding 12
Set Framerate 8
Set Theme "Dracula"
Hide
Type "clear && python3 aws/tui.py" Enter
Sleep 70s
Show
Sleep 20s
Screenshot docs/tui.png
Sleep 1s
Hide
Ctrl+C
