# Training Best Practices — BGMI Killfeed Detection

Ye doc batata hai: kaunsa hyperparameter kyun chuna, kitna time lagega, aur problem aaye to kya karna hai.

## Model Choice: RF-DETR-nano (primary) vs YOLO26-nano (backup)

| | RF-DETR-nano | YOLO26-nano |
|---|---|---|
| **Accuracy** | Zyada (transformer) | Thodi kam (CNN) |
| **Speed (train)** | Slow (~2x) | Fast |
| **Speed (inference CPU)** | Slow | Fast (43% faster than older YOLO) |
| **License** | Apache 2.0 ✅ (commercial-safe) | AGPL-3.0 ⚠️ (bechne pe issue) |
| **Small objects** | Achha (attention) | Bahut achha (STAL built-in) |

**Recommendation:** Dono train karo. Production me RF-DETR use karo (license safe + accurate). YOLO26 backup rakho (CPU pe fast).

## Hyperparameters — Kyun Ye Values?

### RF-DETR-nano

| Param | Value | Reason |
|---|---|---|
| `lr=1e-4` | 0.0001 | RF-DETR official docs ka recommendation. Transformer + AdamW ke saath ye stable hai. Zyada LR se training diverge ho sakti hai. |
| `batch_size=4, grad_accum_steps=4` | Effective 16 | T4 ke 16GB VRAM me batch=4 fit hota hai. Grad accumulation se effective batch 16 milta hai (official: "total batch size 16 maintain karo"). |
| `epochs=15` | 15 | COCO pretrained weights se fine-tune kar rahe hain (scratch se nahi). 100k images × 15 epochs = 1.5M image-passes — kaafi hai. Zyada epochs = overfitting ka risk. |
| `resolution=672` | 672px | RF-DETR ko 56 se divisible resolution chahiye. 672 = 56×12. 640 se bada = chhota killfeed text behtar detect hoga. |
| `optimizer` | AdamW | RF-DETR me built-in. Transformers ke liye SGD se stable hai. |

Sources: [RF-DETR official docs](https://rfdetr.roboflow.com/1.6.1/learn/train/), [ICLR 2026 paper](https://arxiv.org/pdf/2511.09554), [Roboflow benchmarks](https://roboflow.com/blog/yolo26).

### YOLO26-nano

| Param | Value | Reason |
|---|---|---|
| `lr0=0.01` | 0.01 | YOLOv12 paper ka official initial LR (SGD + momentum 0.937). YOLO family me ye standard hai. |
| `lrf=0.01` | Final = 1e-4 | Linear decay: 0.01 → 0.0001. End me chhota LR = fine details pakadta hai. |
| `batch=32` | 32 | Nano model chhota hai, T4 pe 32 aaram se fit hota hai. Bada batch = stable gradients. |
| `epochs=30` | 30 | Fine-tune ke liye. `patience=10` ke saath — agar 10 epochs me improvement nahi, to auto-stop. |
| `imgsz=800` | 800px | Killfeed text chhota hai. 640 se 800 = 56% zyada pixels = behtar small-text detection. T4 pe nano ke liye OK hai. |
| `warmup_epochs=3` | 3 | Pehle 3 epochs LR dheere badhta hai. Training stable hoti hai, early divergence nahi hota. |
| `mosaic=1.0, close_mosaic=10` | — | Mosaic augmentation (4 images mix). Last 10 epochs band — clean data pe final tuning. |
| STAL | Built-in | YOLO26 ka Small-Target-Aware Label Assignment — 8×8px se chhote objects ko minimum 4 anchors milte hain. Killfeed text ke liye perfect. |

Sources: [YOLOv12 paper](https://arxiv.org/html/2502.12524v1/), [YOLO26 docs](https://docs.ultralytics.com/modes/train), [Ultralytics training guide](https://ilirivezaj.com/ai/computer-vision).

## Expected Training Time (T4 GPU, 100k images)

| Model | Per epoch | Total | Notes |
|---|---|---|---|
| RF-DETR-nano | ~25-35 min | **6-10 hours** (15 epochs) | Transformer slow hai |
| YOLO26-nano | ~8-12 min | **4-6 hours** (30 epochs, early stop possible) | CNN fast hai |

**Kaggle limit:** 30 hrs/week free GPU. Dono models ek week me train ho jayenge.

**Agar 5 lakh images hain:** Time ~5x. RF-DETR = 30-50 hours (2 weeks lag sakte hain). Pehle 1 lakh pe train karo, phir decide karo.

## Expected Accuracy (imandaar)

| Metric | Realistic target | Matlab |
|---|---|---|
| mAP@50 | 0.70 – 0.90 | 70-90% killfeed sahi detect |
| Precision | 0.80+ | Galat detection kam |
| Recall | 0.70+ | Miss kam |

**100% kabhi nahi aayega.** Chhota text + blurry frames + naye UI = hamesha kuch miss hoga. 85% + human spotter backup = industry standard.

## Troubleshooting

### "CUDA out of memory"
- Batch size kam karo: RF-DETR me `batch_size=2`, YOLO me `batch=16`.
- Resolution kam karo: RF-DETR `resolution=560`, YOLO `imgsz=640`.

### "Loss NaN ho gaya"
- Learning rate bahut zyada hai. RF-DETR: `lr=5e-5` try karo. YOLO: `lr0=0.005`.
- Warmup epochs badhao.

### "mAP@50 < 0.60"
- Data quality check karo: kya labels sahi hain? 50 random frames dekho.
- Aur diverse data chahiye (alag tournaments, alag quality).
- Resolution badhao (YOLO: `imgsz=960`).

### "Training bahut slow hai"
- Normal hai RF-DETR ke liye. YOLO try karo (fast hai).
- Kaggle pe T4x2 select karo agar available ho.

### "Kaggle session disconnect ho gaya"
- Har 2-3 epochs me checkpoint save hota hai. Wahan se resume karo.
- `resume=True` add karo train call me.

## Model Achha Hai Ya Nahi? (Checklist)

- [ ] mAP@50 > 0.70 (validation set pe)
- [ ] Precision > 0.80 (false alarm kam)
- [ ] 20 random test frames pe visually check kiya
- [ ] ONNX export ho gaya
- [ ] AEGIS me load karke 1 test video pe chalaya

Sab ✅ to model ready hai. Koi ❌ to upar troubleshooting dekho.
