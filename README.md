# Whisper Dictation

A lightweight macOS dictation tool that uses OpenAI Whisper to transcribe your voice and type the result into any app. Runs silently as a menu bar app — no terminal needed.

- **Hold fn** or **middle mouse button** to record
- **Release** to transcribe and type
- Menu bar icon shows status: 🎤 idle · 🔴 recording · ⏳ transcribing · 📞 external transcription running

---

## Requirements

- macOS (Apple Silicon recommended)
- Python 3.10+
- Microsoft Word (for PDF export, optional)

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/matthumble/whisper-dictation.git
cd whisper-dictation
```

### 2. Download the Whisper model

The tool uses the `small` Whisper model by default. Create a folder for the model and set the path in `dictation.py`:

```bash
mkdir -p ~/whisper-models
```

The model will download automatically the first time the app runs. To pre-download it manually:

```bash
python3 -c "import whisper; whisper.load_model('small', download_root='<path-to-your-models-folder>')"
```

Update `WHISPER_MODEL_DIR` in `dictation.py` to point to your models folder:

```python
WHISPER_MODEL_DIR = Path.home() / "whisper-models"
```

### 3. Create a virtual environment and install dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 4. Test it manually first

```bash
.venv/bin/python3 dictation.py
```

You should see 🎤 appear in your menu bar. If you get an accessibility warning, follow the permissions step below.

### 5. Grant Accessibility permission

macOS requires Accessibility access for the tool to read keyboard and mouse input.

Go to **System Settings → Privacy & Security → Accessibility** and add:

```
<path-to-your-project>/.venv/bin/python3.x
```

You may also be prompted for **Microphone** access the first time you record.

### 6. Install as a launchd service (auto-start on login)

Copy the example plist, update the paths inside it, then load it:

```bash
cp com.example.dictation.plist ~/Library/LaunchAgents/com.yourname.dictation.plist
```

Edit the plist to point to your Python binary and `dictation.py` path, then:

```bash
launchctl load ~/Library/LaunchAgents/com.yourname.dictation.plist
```

To restart after changes:

```bash
launchctl kickstart -k gui/$(id -u)/com.yourname.dictation
```

---

## Configuration

All settings are at the top of `dictation.py`:

| Setting | Default | Description |
|---|---|---|
| `WHISPER_MODEL_DIR` | `~/whisper-models` | Path to Whisper model files |
| `MODEL_SIZE` | `small` | Whisper model: `tiny`, `base`, `small`, `medium`, `large`, `turbo` |
| `MIN_DURATION_SEC` | `0.5` | Ignore clips shorter than this (seconds) |
| `EXTERNAL_TRANSCRIPTION_PATTERNS` | `whisper`, `macwhisper`, `transcribe`, etc. | Process-name matches that make dictation show 📞 and ignore new recording starts |

When 📞 appears, dictation is intentionally unavailable because another Whisper-style transcription process appears to be running. This avoids competing with your larger call-transcription model for local compute.

### Changing the hotkey

By default the tool uses **fn** and **middle mouse**. To change the mouse button, update `on_mouse_click`:

```python
if button == mouse.Button.middle:  # change to .left, .right, etc.
```

The fn key is hardcoded via Quartz. To disable it entirely, remove the `_start_fn_listener` thread from `__main__`.

---

## Stopping the tool

Click **🎤** in the menu bar and select **Quit Dictation**.

If running as a launchd service it will restart automatically (by design). To stop it permanently:

```bash
launchctl unload ~/Library/LaunchAgents/com.yourname.dictation.plist
```

---

## Troubleshooting

**No menu bar icon appears**
- Check `dictation.log` for errors
- Verify Accessibility permission is granted

**fn key not working**
- Make sure Accessibility permission is granted for the correct Python binary
- Some keyboards remap fn — try reassigning to a different trigger

**Transcription is slow**
- Switch to `tiny` or `base` model for faster (less accurate) results
- The first transcription after startup is slower due to model warmup

**Text pastes in wrong app**
- Release the key slowly — there is a small delay before paste fires

**Python keeps appearing in the Dock**
- This happens when macOS treats the host interpreter as a normal foreground app instead of a menu bar accessory
- The current `dictation.py` overrides `rumps` startup to force accessory mode so only the menu bar icon stays visible

**Quit from the menu bar just reopens**
- If your LaunchAgent plist uses `<key>KeepAlive</key><true/>`, launchd will restart the app after it exits
- Remove `KeepAlive` if you want Quit to stop the app until the next manual launch or login
