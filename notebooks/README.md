# AEGIS AI — Training Notebooks 📓

Kaggle (free GPU) par kill-feed detection model train karne wale notebooks. Poora step-by-step tarika: [`docs/KAGGLE_TRAINING.md`](../docs/KAGGLE_TRAINING.md).

## Kaunsa notebook kab chalao (isi order me)

| # | Notebook | Kya hai |
|---|----------|---------|
| 1 | [`train_rfdetr_nano_killfeed.ipynb`](train_rfdetr_nano_killfeed.ipynb) | **PRIMARY** — RF-DETR-Nano (Apache 2.0, commercial-safe). Pehle ye chalao. |
| 2 | [`train_yolo26_nano_killfeed.ipynb`](train_yolo26_nano_killfeed.ipynb) | **ALTERNATIVE** — YOLO26-nano (CPU-friendly; iska ONNX seedha `VISION_MODEL_PATH` me lagta hai). Dusre number par. |
| 3 | [`qwen3_vl_director.ipynb`](qwen3_vl_director.ipynb) | **OPTIONAL / advanced** — Qwen3-VL se director-style reasoning (inference + LoRA outline). Sirf agar chaho. |

Har notebook Roman-script Hinglish me likha hai: Kaggle account → GPU on → Run dabao.

> Note: Purana Google Colab notebook (`train_yolov8_killfeed.ipynb`) **hata diya gaya hai** — user decision ke baad saari training Kaggle par hogi.
