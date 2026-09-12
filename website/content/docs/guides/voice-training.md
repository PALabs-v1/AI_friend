# Voice Training on GPU

To achieve high-quality, expressive voice cloning with custom prosody and emotion, you can fine-tune GPT-SoVITS on your own voice samples.

---

## Reference Dataset Preparation

1. Record **1 to 3 minutes** of clean, dry voice audio using an external microphone.
2. Segment the recording into **5 to 10 second WAV clips**.
3. Generate exact transcripts for each clip.

---

## 1-Click Google Colab Training

Rather than running heavy PyTorch CUDA training on your local laptop, use the bundled Google Colab notebook:

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/PALabs-v1/AI_friend/blob/main/notebooks/01_voice_clone_sovits_training.ipynb)

### Colab Training Steps:
1. Open [`notebooks/01_voice_clone_sovits_training.ipynb`](https://github.com/PALabs-v1/AI_friend/blob/main/notebooks/01_voice_clone_sovits_training.ipynb) in Colab.
2. Select **Runtime $\rightarrow$ Change runtime type $\rightarrow$ T4 GPU**.
3. Upload your audio clips and run the automated pipeline (Hubert feature extraction, Semantic token extraction, and SoVITS fine-tuning).
4. Download the generated weights (`my_voice.pth` and `my_voice.ckpt`).

---

## Installing Trained Weights Locally

Place your trained weights in `backend/voice_samples/` and update your `.env`:

```ini
REF_AUDIO_PATH=backend/voice_samples/my_voice_ref.wav
REF_TEXT="Hello, this is my trained custom voice speaking."
```

Restart the `voice_agent` container:
```bash
docker compose -f docker-compose.infra.yml -f docker-compose.prod.yml restart voice_agent
```
