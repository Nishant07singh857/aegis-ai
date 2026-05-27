# backend/app/ml/commentary/fine_tuning/trainer.py
"""
AEGIS AI - Commentary Fine-Tuning Trainer
Complete fine-tuning pipeline for commentary LLM.
Uses LoRA/QLoRA for efficient training on consumer GPUs.
"""

import os
import json
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import structlog
import torch
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainingArguments,
    Trainer,
    BitsAndBytesConfig,
)
from peft import (
    LoraConfig,
    get_peft_model,
    prepare_model_for_kbit_training,
    TaskType,
)

from .dataset_builder import CommentaryDataset, DataFormat

logger = structlog.get_logger("aegis.ml.finetune.trainer")


@dataclass
class FineTuningConfig:
    """Fine-tuning configuration"""
    # Model
    base_model: str = "meta-llama/Llama-2-7b-chat-hf"
    output_dir: str = "fine_tuned_models/aegis-commentary"
    output_name: str = "aegis-commentary-v1"
    
    # LoRA
    use_lora: bool = True
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.1
    lora_target_modules: List[str] = field(default_factory=lambda: [
        "q_proj", "v_proj", "k_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ])
    
    # Quantization (QLoRA)
    use_4bit: bool = True
    bnb_4bit_compute_dtype: str = "float16"
    bnb_4bit_quant_type: str = "nf4"
    bnb_4bit_use_double_quant: bool = True
    
    # Training
    num_epochs: int = 3
    batch_size: int = 4
    gradient_accumulation_steps: int = 4
    learning_rate: float = 2e-4
    warmup_ratio: float = 0.03
    weight_decay: float = 0.01
    max_grad_norm: float = 1.0
    
    # Sequence
    max_seq_length: int = 512
    
    # Saving
    save_steps: int = 500
    eval_steps: int = 200
    logging_steps: int = 50
    save_total_limit: int = 3
    
    # Performance
    fp16: bool = True
    bf16: bool = False
    dataloader_num_workers: int = 4
    
    # Dataset
    train_file: str = "data/train.jsonl"
    val_file: str = "data/val.jsonl"
    data_format: DataFormat = DataFormat.ALPACA
    
    # Generation
    max_new_tokens: int = 150
    temperature: float = 0.8
    top_p: float = 0.95
    
    # Hardware
    device: str = "cuda"
    use_flash_attention: bool = True


class CommentaryTrainer:
    """
    Fine-tuning trainer for commentary LLM.
    
    Features:
    - LoRA/QLoRA for efficient fine-tuning
    - 4-bit quantization for consumer GPUs
    - Flash Attention 2 support
    - Gradient checkpointing
    - Mixed precision training
    - Evaluation during training
    - Checkpoint saving and resumption
    """
    
    def __init__(self, config: Optional[FineTuningConfig] = None):
        self.config = config or FineTuningConfig()
        
        # Core components
        self.model: Optional[AutoModelForCausalLM] = None
        self.tokenizer: Optional[AutoTokenizer] = None
        self.trainer: Optional[Trainer] = None
        
        # Datasets
        self.train_dataset: Optional[CommentaryDataset] = None
        self.val_dataset: Optional[CommentaryDataset] = None
        
        # Statistics
        self._training_start_time: float = 0.0
        self._total_steps: int = 0
        
        self._log = logger.bind(component="commentary_trainer")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MODEL SETUP
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def setup_model(self) -> None:
        """Setup model and tokenizer with LoRA"""
        
        self._log.info(f"Loading base model: {self.config.base_model}")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Quantization config (4-bit QLoRA)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        bnb_config = None
        
        if self.config.use_4bit:
            compute_dtype = getattr(torch, self.config.bnb_4bit_compute_dtype)
            
            bnb_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type=self.config.bnb_4bit_quant_type,
                bnb_4bit_compute_dtype=compute_dtype,
                bnb_4bit_use_double_quant=self.config.bnb_4bit_use_double_quant,
            )
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Load model
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        model_kwargs = {
            "device_map": "auto",
            "trust_remote_code": True,
        }
        
        if bnb_config:
            model_kwargs["quantization_config"] = bnb_config
        
        if self.config.use_flash_attention:
            model_kwargs["use_flash_attention_2"] = True
        
        self.model = AutoModelForCausalLM.from_pretrained(
            self.config.base_model,
            **model_kwargs,
        )
        
        # Enable gradient checkpointing
        self.model.gradient_checkpointing_enable()
        
        # Prepare for k-bit training
        if self.config.use_4bit:
            self.model = prepare_model_for_kbit_training(self.model)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Load tokenizer
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.config.base_model,
            trust_remote_code=True,
        )
        
        # Set padding token
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        
        self.tokenizer.padding_side = "right"
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # LoRA configuration
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        if self.config.use_lora:
            lora_config = LoraConfig(
                r=self.config.lora_r,
                lora_alpha=self.config.lora_alpha,
                target_modules=self.config.lora_target_modules,
                lora_dropout=self.config.lora_dropout,
                bias="none",
                task_type=TaskType.CAUSAL_LM,
            )
            
            self.model = get_peft_model(self.model, lora_config)
            
            # Print trainable parameters
            trainable_params = sum(
                p.numel() for p in self.model.parameters() if p.requires_grad
            )
            total_params = sum(p.numel() for p in self.model.parameters())
            
            self._log.info(
                f"LoRA configured: {trainable_params:,} trainable / "
                f"{total_params:,} total ({100 * trainable_params / total_params:.2f}%)"
            )
        
        self._log.info("Model setup complete")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DATA LOADING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def load_data(
        self,
        train_examples: List[Any],
        val_examples: Optional[List[Any]] = None,
    ) -> None:
        """Load training and validation data"""
        
        self.train_dataset = CommentaryDataset(
            examples=train_examples,
            tokenizer=self.tokenizer,
            max_length=self.config.max_seq_length,
            format=self.config.data_format,
        )
        
        if val_examples:
            self.val_dataset = CommentaryDataset(
                examples=val_examples,
                tokenizer=self.tokenizer,
                max_length=self.config.max_seq_length,
                format=self.config.data_format,
            )
        
        self._log.info(
            f"Data loaded: train={len(self.train_dataset)}, "
            f"val={len(self.val_dataset) if self.val_dataset else 0}"
        )
    
    def load_data_from_files(self) -> None:
        """Load data from configured file paths"""
        
        from .dataset_builder import DatasetBuilder
        
        builder = DatasetBuilder()
        
        # Load training data
        if os.path.exists(self.config.train_file):
            builder.load_from_jsonl(self.config.train_file, self.config.data_format)
            train_examples = builder.examples
        else:
            self._log.error(f"Training file not found: {self.config.train_file}")
            train_examples = []
        
        # Load validation data
        val_examples = None
        if os.path.exists(self.config.val_file):
            val_builder = DatasetBuilder()
            val_builder.load_from_jsonl(self.config.val_file, self.config.data_format)
            val_examples = val_builder.examples
        
        self.load_data(train_examples, val_examples)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TRAINING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def train(self) -> Dict[str, Any]:
        """Run fine-tuning training"""
        
        if not self.model or not self.train_dataset:
            self._log.error("Model or data not loaded")
            return {"error": "Model or data not loaded"}
        
        self._log.info("Starting fine-tuning...")
        self._training_start_time = time.time()
        
        # Calculate total steps
        total_batch_size = (
            self.config.batch_size *
            self.config.gradient_accumulation_steps
        )
        
        steps_per_epoch = len(self.train_dataset) // total_batch_size
        total_steps = steps_per_epoch * self.config.num_epochs
        warmup_steps = int(total_steps * self.config.warmup_ratio)
        
        self._log.info(
            f"Training config: {self.config.num_epochs} epochs, "
            f"{total_steps} total steps, {warmup_steps} warmup steps, "
            f"effective batch size: {total_batch_size}"
        )
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Training arguments
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        training_args = TrainingArguments(
            output_dir=self.config.output_dir,
            num_train_epochs=self.config.num_epochs,
            per_device_train_batch_size=self.config.batch_size,
            per_device_eval_batch_size=self.config.batch_size,
            gradient_accumulation_steps=self.config.gradient_accumulation_steps,
            learning_rate=self.config.learning_rate,
            warmup_ratio=self.config.warmup_ratio,
            weight_decay=self.config.weight_decay,
            max_grad_norm=self.config.max_grad_norm,
            
            # Logging
            logging_steps=self.config.logging_steps,
            logging_dir=os.path.join(self.config.output_dir, "logs"),
            
            # Saving
            save_steps=self.config.save_steps,
            save_total_limit=self.config.save_total_limit,
            
            # Evaluation
            evaluation_strategy="steps" if self.val_dataset else "no",
            eval_steps=self.config.eval_steps if self.val_dataset else None,
            
            # Mixed precision
            fp16=self.config.fp16,
            bf16=self.config.bf16,
            
            # Performance
            dataloader_num_workers=self.config.dataloader_num_workers,
            gradient_checkpointing=True,
            optim="paged_adamw_8bit" if self.config.use_4bit else "adamw_torch",
            
            # Reporting
            report_to=["tensorboard"],
            
            # Save only model weights
            save_only_model=True,
        )
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Create trainer
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        self.trainer = Trainer(
            model=self.model,
            args=training_args,
            train_dataset=self.train_dataset,
            eval_dataset=self.val_dataset,
            tokenizer=self.tokenizer,
            data_collator=self._data_collator,
        )
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Train
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        train_result = self.trainer.train()
        
        # Save final model
        final_path = os.path.join(self.config.output_dir, "final")
        self.trainer.save_model(final_path)
        self.tokenizer.save_pretrained(final_path)
        
        # Save config
        config_path = os.path.join(final_path, "training_config.json")
        with open(config_path, 'w') as f:
            json.dump(self.config.__dict__, f, indent=2, default=str)
        
        training_time = time.time() - self._training_start_time
        
        self._log.info(
            f"Training complete! Time: {training_time:.0f}s, "
            f"Loss: {train_result.training_loss:.4f}"
        )
        
        return {
            "training_loss": train_result.training_loss,
            "training_time_seconds": training_time,
            "total_steps": train_result.global_step,
            "model_saved_to": final_path,
        }
    
    def _data_collator(self, batch: List[Dict[str, torch.Tensor]]) -> Dict[str, torch.Tensor]:
        """Collate batch for training"""
        
        input_ids = torch.stack([item["input_ids"] for item in batch])
        attention_mask = torch.stack([item["attention_mask"] for item in batch])
        labels = torch.stack([item["labels"] for item in batch])
        
        # Mask padding tokens in labels
        labels[labels == self.tokenizer.pad_token_id] = -100
        
        return {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "labels": labels,
        }
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INFERENCE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def generate(
        self,
        prompt: str,
        max_new_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> Optional[str]:
        """Generate commentary from prompt"""
        
        if not self.model or not self.tokenizer:
            self._log.error("Model not loaded")
            return None
        
        max_tokens = max_new_tokens or self.config.max_new_tokens
        temp = temperature or self.config.temperature
        top = top_p or self.config.top_p
        
        # Tokenize
        inputs = self.tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=self.config.max_seq_length,
        ).to(self.model.device)
        
        # Generate
        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                temperature=temp,
                top_p=top,
                do_sample=True,
                pad_token_id=self.tokenizer.pad_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )
        
        # Decode
        text = self.tokenizer.decode(
            outputs[0][inputs["input_ids"].shape[1]:],
            skip_special_tokens=True,
        )
        
        return text.strip()
    
    def generate_batch(
        self,
        prompts: List[str],
        **kwargs,
    ) -> List[Optional[str]]:
        """Generate commentary for multiple prompts"""
        return [self.generate(prompt, **kwargs) for prompt in prompts]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MODEL MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def save_model(self, path: str) -> None:
        """Save model and tokenizer"""
        
        if not self.model or not self.tokenizer:
            return
        
        os.makedirs(path, exist_ok=True)
        
        self.model.save_pretrained(path)
        self.tokenizer.save_pretrained(path)
        
        self._log.info(f"Model saved to {path}")
    
    def load_model(self, path: str) -> bool:
        """Load fine-tuned model"""
        
        try:
            self.model = AutoModelForCausalLM.from_pretrained(
                path,
                device_map="auto",
                trust_remote_code=True,
            )
            
            self.tokenizer = AutoTokenizer.from_pretrained(
                path,
                trust_remote_code=True,
            )
            
            self._log.info(f"Model loaded from {path}")
            return True
            
        except Exception as e:
            self._log.error(f"Failed to load model: {e}")
            return False
    
    def merge_and_save(self, output_path: str) -> bool:
        """Merge LoRA weights and save full model"""
        
        if not self.config.use_lora:
            self._log.warning("Not a LoRA model - nothing to merge")
            return False
        
        try:
            # Merge LoRA weights
            merged_model = self.model.merge_and_unload()
            
            # Save
            merged_model.save_pretrained(output_path)
            self.tokenizer.save_pretrained(output_path)
            
            self._log.info(f"Merged model saved to {output_path}")
            return True
            
        except Exception as e:
            self._log.error(f"Failed to merge model: {e}")
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get trainer statistics"""
        return {
            "model": self.config.base_model,
            "lora_rank": self.config.lora_r if self.config.use_lora else 0,
            "quantization": "4-bit" if self.config.use_4bit else "none",
            "training_time": round(time.time() - self._training_start_time, 1) if self._training_start_time > 0 else 0,
            "total_steps": self._total_steps,
        }
    
    def reset(self) -> None:
        """Reset trainer"""
        self.model = None
        self.tokenizer = None
        self.trainer = None
        self.train_dataset = None
        self.val_dataset = None
        self._training_start_time = 0.0
        self._total_steps = 0
        self._log.info("Trainer reset")