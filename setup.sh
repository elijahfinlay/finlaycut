#!/bin/bash
# FinlayCut Easy Setup - just run this and follow the prompts

echo ""
echo "  ╔═══════════════════════════════╗"
echo "  ║     FinlayCut Easy Setup      ║"
echo "  ╚═══════════════════════════════╝"
echo ""

# Step 1: Install Python AI packages
echo "Step 1/3: Installing AI packages (this takes a few minutes)..."
echo ""
cd "$(dirname "$0")/backend"
source .venv/bin/activate

pip install --quiet stable-ts openai-whisper librosa Pillow numpy 2>&1 | grep -v "already satisfied"
echo "  Done!"
echo ""

# Step 2: Install Ollama (free local AI)
echo "Step 2/3: Setting up Ollama (free local AI)..."
if command -v ollama &> /dev/null; then
    echo "  Ollama already installed!"
else
    echo "  Installing Ollama..."
    brew install ollama 2>/dev/null || {
        echo ""
        echo "  Downloading Ollama installer..."
        curl -fsSL https://ollama.com/install.sh | sh
    }
fi

# Start Ollama and pull a model
echo "  Starting Ollama..."
ollama serve &>/dev/null &
sleep 2
echo "  Downloading AI model (one-time, ~4GB)..."
ollama pull llama3 2>&1 | tail -1
echo "  Done!"
echo ""

# Step 3: Done!
echo "Step 3/3: All set!"
echo ""
echo "  ╔═══════════════════════════════════════════╗"
echo "  ║  Setup complete! Here's what to do:       ║"
echo "  ║                                           ║"
echo "  ║  1. Open DaVinci Resolve                  ║"
echo "  ║  2. Go to: DaVinci Resolve menu           ║"
echo "  ║     → Preferences → System → General      ║"
echo "  ║     → Set 'External scripting using'       ║"
echo "  ║       to 'Local'                           ║"
echo "  ║  3. Click Save, then restart Resolve       ║"
echo "  ║  4. Open a project with a timeline         ║"
echo "  ║  5. Come back here and run:                ║"
echo "  ║                                           ║"
echo "  ║     cd ~/finlaycut && npm run dev          ║"
echo "  ║                                           ║"
echo "  ╚═══════════════════════════════════════════╝"
echo ""
