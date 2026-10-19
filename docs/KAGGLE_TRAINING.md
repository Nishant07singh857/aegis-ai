# AEGIS AI — Kaggle par Model Training (Step-by-Step Guide) 🚀

Ye guide **non-technical** bhasha me batati hai ki kill-feed detection model ko **Kaggle ke free GPU** par kaise train karna hai — account banane se lekar trained model ko AEGIS server me lagane tak.

**Pehle ye samajh lo (2 minute):**
- Hamara dataset **100,000 frames** ka hai, **10 chunk zips** me aayega (~800MB each: images + labels).
- Training **Kaggle** par hogi (free GPU: hafte me ~30 ghante, Tesla T4).
- 3 notebooks hain (`notebooks/` folder me) — neeche bataya hai kaunsa kab chalana hai.
- Aakhri me ek `best.onnx` file milegi — wahi AEGIS ke vision pipeline me lagegi.

---

## Step 0 — Kaggle account banao 👤

1. [kaggle.com](https://www.kaggle.com) kholo → **Sign Up** (Google se bhi ho jata hai).
2. **Phone number verify karna ZAROORI hai** — bina phone verification ke Kaggle free GPU quota nahi deta. Kaggle → apne avatar → **Account** → phone verify karo.
3. Verify hone ke baad **+ Create** → **Notebook** kholo — ye hamari training wali jagah hai.

## Step 1 — Notebook me GPU on karo 🔌

Notebook khulne ke baad right side me **Settings** (gear icon) me jao:

- **Accelerator** → **GPU T4** select karo (kabhi "GPU T4 x2" bhi dikhta hai — wahi le lo).
- **Internet** → ON rakho (pip install ke liye zaroori hai).
- **Persistence** → default rehne do.

**Quota note (paise wali baat nahi, time wali hai):** Kaggle hafte me **~30 ghante free GPU** deta hai — poori tarah free, paise nahi lagte. Ye quota Kaggle → Settings me dikhta hai. 100k images × 50 epochs me **kai ghante** lagenge, isliye training shuru karne se **pehle** bacha hua quota dekh lo.

**Session limit (imaandaar note):** Ek GPU session **max ~9-12 ghante** chalta hai (Kaggle ke docs me 12 ghante likha hai, GPU tips page par 9 ghante) — uske baad session apne aap band ho jata hai. Ghabrana nahi: hamare notebooks **checkpoints** save karte rehte hain, to beech me ruki training **wahi se resume** ho jati hai (tarika notebooks me likha hai).

## Step 2 — Dataset Kaggle par upload karo 📦 (chunk zips)

Dataset 10 chunk zips me aayega (`chunk_01.zip` … `chunk_10.zip`, har ek ~800MB). Inhe Kaggle par **Kaggle API** se upload karna sabse aasan hai. Commands **isi order** me chalao (apne computer ke terminal me):

> **Test split note:** Chunks me `train/` aur `val/` ke saath **`test/` folder** bhi hona chahiye (500-1000 clean frames, ek bilkul alag tournament se — training me kabhi use nahi hoga). Notebook isko khud pehchan lega; agar na mile to Hinglish warning dega (fail nahi hoga).

```bash
# 1. Kaggle API tool install karo
pip install kaggle

# 2. API token lo: kaggle.com kholo → apne avatar par click → Account →
#    "Create New API Token" dabao → kaggle.json download hogi
# 3. Token ko sahi jagah rakho (apne download folder ka path badal lena):
mkdir -p ~/.kaggle
cp ~/Downloads/kaggle.json ~/.kaggle/kaggle.json
chmod 600 ~/.kaggle/kaggle.json
```

Ab do plans hain — **Plan A recommended** hai:

### Plan A — Ek dataset + chunks as versions (saaf-suthra) ✅

```bash
# 4. Pehle chunk se dataset banao
mkdir -p ~/aegis_upload
cp /path/to/chunk_01.zip ~/aegis_upload/     # apna asli path likho
kaggle datasets init -p ~/aegis_upload       # dataset-metadata.json banegi

# 5. dataset-metadata.json kholo aur aise edit karo:
#    {
#      "title": "AEGIS Killfeed Dataset",
#      "id": "USERNAME/aegis-killfeed-dataset",
#      "licenses": [{"name": "CC0-1.0"}]
#    }
#    (USERNAME ki jagah apna Kaggle username likho)

# 6. Dataset create karo
kaggle datasets create -p ~/aegis_upload

# 7. Baaki 9 chunks ek-ek karke naye version me jodho:
cp /path/to/chunk_02.zip ~/aegis_upload/
kaggle datasets version -p ~/aegis_upload -m "add chunk_02 (10k frames)"
cp /path/to/chunk_03.zip ~/aegis_upload/
kaggle datasets version -p ~/aegis_upload -m "add chunk_03 (10k frames)"
# ... chunk_10 tak aise hi dohrao
```

**Imaandaar note:** Har `version` command poora folder **dobara** upload karta hai, to kul upload ~44GB tak ho jayega (10 chunks ke liye). Agar aapka internet slow hai ya bandwidth kam hai to **Plan B** lo.

### Plan B — 10 alag datasets (bandwidth bachao) ✅

Har chunk ko apna dataset banao (`aegis-killfeed-chunk01` … `chunk10`) — kul upload sirf ~8GB hoga. **Notebook me koi change nahi chahiye**: wo `/kaggle/input` ke **saare** zip chunks khud dhoondh leta hai — bas notebook me 10 datasets attach karne honge (Step 3 me 10 baar "+ Add data").

### Dataset size limit ke baare me sach

Kaggle ne datasets ke liye **koi chhota hard size limit publicly document nahi kiya** — platform par kai-GB (dashkon GB) wale datasets aam hain. Hamara kul **~8GB** hai, jo aaram se ek dataset me sama jayega. Agar version upload karte waqt size se juda koi error aaye to **Plan B** istemal karo — notebook dono surat me bina change ke chalega.

## Step 3 — Dataset notebook se jodho 🔗

1. Kaggle me **+ Create** → **Notebook** kholo (ya hamari `.ipynb` file **File → Upload notebook** se kholo).
2. Right side **+ Add data** → **Your Datasets** → `aegis-killfeed-dataset` search karke **Add** dabao. (Plan B me 10 chunk datasets add karo.)
3. Notebook ke **Cell 3** me `DATASET_SLUG` me apna Kaggle username likho:
   ```python
   DATASET_SLUG = "apna-username/aegis-killfeed-dataset"
   ```

## Step 4 — Notebooks chalao 🏋️ (isi order me)

| # | Notebook | Kya hai | Kab chalao |
|---|----------|---------|------------|
| 1 | `train_rfdetr_nano_killfeed.ipynb` | **PRIMARY** model (RF-DETR-Nano, Apache 2.0 license — commercial-safe) | Pehle ye |
| 2 | `train_yolo26_nano_killfeed.ipynb` | **ALTERNATIVE** (YOLO26-nano, CPU-friendly; iska ONNX seedha lagta hai) | Uske baad |
| 3 | `qwen3_vl_director.ipynb` | **OPTIONAL/advanced** — director reasoning (vision-language model) | Sirf agar chaho |

Har notebook me upar se neeche tak **Run** dabate jao. Pehle cell me `!pip install` hai (~2-4 min), phir GPU check, phir chunk unzip (~10-15 min, 8GB hai), phir training.

**Training time (imaandaar note):** 100,000 images × 50 epochs T4 par **kai ghante** lagenge — exact time pehle se nahi bataya ja sakta; Run ke baad jo ETA dikhe use dekho. Session 9-12 ghante me band ho jata hai — **checkpoints** `/kaggle/working/` me safe rehte hain:
- RF-DETR: `/kaggle/working/rfdetr_output/` me `.pth` → resume ke liye training cell ki `resume=` wali line uncomment karo.
- YOLO: `/kaggle/working/runs/aegis_killfeed/weights/last.pt` → resume ke liye `resume=True` lagao.
- Time kam ho to `epochs=50` ko 25-30 kar sakte ho — **default 50 hi rakha hai**.

## Step 5 — `best.onnx` download karo ⬇️

Training + evaluation + export ke baad `/kaggle/working/best.onnx` ban jayegi. Notebook page ke right side **Output** panel se use download karo (ya **File → Download**).

**Kaunsa best.onnx rakhu?** Dono notebooks chalane ke baad results compare karo (precision/recall/mAP) — jo behtar ho, wahi final. Ek practical farak yaad rakho:
- **YOLO wala ONNX** seedha AEGIS ke `VISION_MODEL_PATH` me lagta hai — koi extra kaam nahi.
- **RF-DETR wala ONNX** raw object queries deta hai (har query ke liye box + class scores) — is par threshold + box scaling ka **chhota post-processing wrapper** backend me chahiye hoga. (Agar ye wrapper abhi nahi bana hai to YOLO wala istemal karo.)

## Step 6 — AEGIS server me lagao 🔧

1. `best.onnx` ko server par kisi fixed jagah rakho, example: `/opt/aegis/models/killfeed_best.onnx`.
2. Backend ke environment me ye variable set karo:
   ```bash
   VISION_MODEL_PATH=/opt/aegis/models/killfeed_best.onnx
   ```
3. Backend **restart** karo (taaki naya model load ho jaye).

**Fallback chain (pehle se backend me bana hai):** AEGIS ka vision wiring (`backend/app/ml/vision_wiring.py`) aise kaam karta hai:
`trained model` → (na mile to) `heuristic` → (wo bhi na chale to) `"unknown"`.
Matlab: `VISION_MODEL_PATH` set nahi hai ya file missing hai to system **purane heuristic detector** par chalta rahega — kuch tootega nahi, bas trained model ka fayda nahi milega.

## Accuracy expectations (bina jhoothe vaade) 📊

- **Koi accuracy number pehle se promise nahi kiya ja sakta** — asli precision/recall/mAP training ke baad evaluation cell me dikhega, wahi sach hai.
- Itna keh sakte hain: **100,000 frames kaafi bada dataset** hai, aur kill-feed **single class + screen par lagbhag fixed jagah** par aata hai — ye aasan detection tasks me gina jata hai.
- **Seedha niyam:** Data zyada + alag-alag tarah ke frames (alag maps, alag kill-feed styles, din/raat) = behtar model. Galat detections dikhen to waise frames dataset me jodho aur dobara train karo (dataset me `kaggle datasets version -m "..."` se naya version jodho).

## 95%+ ka rasta (bina jhoothe vaade) 🛤️

"95%+ accuracy" ki **koi guarantee nahi hai — koi bhi number pehle se promise nahi kiya ja sakta**. Lekin ye rasta pakka hai: is par chaloge to model lagatar behtar hota jayega —

1. **Diversity (8-10 tournaments):** Sirf ek tournament ke frames par train karoge to model sirf wahi style pehchanega. 8-10 **alag tournaments** ke frames jodho — alag casters, alag overlays, alag kill-feed designs.
2. **Mixed quality:** 720p aur 1080p dono rakho, alag bitrates rakho, aur thode **noisy frames jaanboojh kar** rakho (motion blur, transition wale frames, halka glitch) — asli broadcast saaf-suthra nahi hota, model ko wahi duniya dikhao.
3. **Hard negatives:** Aise frames jodho jahan kill-feed **jaisa** text dikhta hai lekin kill-feed **nahi** hai (scoreboard, chat box, player name plates) — inse model ke galat alarm kam honge.
4. **Clean held-out test set:** 500-1000 frames, **ek bilkul alag tournament** se, jo training me **kabhi** use nahi hue. Yehi "final report card" hai — isi par mapa gaya number **asli duniya ka sabse imaandaar andaza** hai.
5. **Iterate karo:** Test set par jahan model galat kare, waise frames dataset me jodho → `kaggle datasets version -m "..."` se naya version banao → dobara train karo. Har iteration me test number upar jana chahiye.

**Seedha niyam yaad rakho:** number **measure** karo, **assume** mat karo. Jo test set par dikhe, wahi sach hai.

## Troubleshooting 🩹

| Dikkat | Wajah | Ilaaj |
|--------|-------|-------|
| `CUDA available: False` | GPU on nahi hai | Settings → Accelerator → **GPU T4** select karo |
| `GPU quota exhausted` | Hafte ke ~30 ghante khatm | Agle hafte ka wait karo, ya epochs ghatao (25-30) |
| `Koi zip chunk nahi mila` | Dataset attach nahi kiya | Right side **+ Add data** → dataset add karo |
| Session beech me band (9-12h) | Kaggle time limit | Checkpoints se **resume** karo (Step 4 dekho) |
| `CUDA out of memory` | Batch bada hai | RF-DETR: `batch_size=4` → 2 karo; YOLO: `batch=16` → 8 karo |
| `No space left on device` | Disk bhar gaya | `/kaggle/working` me purani/bekaar files delete karo |
| `pip install` ke baad import error | Kernel purana env pakde hai | **Kernel → Restart** karke cells dobara chalao |
| `kaggle.json` permission error | Token file galat jagah/mode | `~/.kaggle/kaggle.json` + `chmod 600` dobara karo |
| ONNX export fail (RF-DETR) | export extra missing | Cell 1 me `"rfdetr[onnxexport]"` install hua? Check karo |

---

*Related: `notebooks/README.md` (notebook list), `notebooks/train_rfdetr_nano_killfeed.ipynb`, `notebooks/train_yolo26_nano_killfeed.ipynb`, `notebooks/qwen3_vl_director.ipynb`.*
