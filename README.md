Many people thought it was impossible to make a worse voice assistant than Siri. I accept that 
challenge.

STT is handled by a hard-coded faster-whisper, probably moving to Parakeet for Mac (since
faster-whisper uses CUDA).

TTS is currently handled by Kokoro and not satisfactory, so I've made it **off** by default.
It can be turned on in the settings panel. 

## Repo breakdown

_tbc_

## Installing

### With the script

Run
```powershell
.\install.bat          # CPU
.\install.bat gpu      # + NVIDIA CUDA speech-to-text
```

### Manually

You'd need `Python 3.12+`. I recommended that you use an isolated environment (`.venv`) so
installs do not touch the system Python.

First, **enable `Long Paths`**, because PySide6 (which drives the UI) nests QML module
trees exceeding 260 characters. The install currently silently fails without it. After install,
run `python -m frontend` to check for this at startup.

Then, create a `.venv` and install CUDA for NVIDIA GPUS:

```bash
python3.12 -m venv .venv          # Windows: py -3.12 -m venv .venv (optional)
source .venv/bin/activate         # Windows: .venv\Scripts\activate (if .venv is used)
pip install -e .
pip install -e ".[gpu-cuda]"      # optional: NVIDIA GPU speech-to-text (~28x faster)
```

Then, to start, start the assistant with:

```bash
source .venv/bin/activate         # Windows: .venv\Scripts\activate (optional)
python run.py                     # start BOTH — daemon + Teleprompter — in one window; Ctrl-C stops both
```

Some custom checker commands:

_tbc_
