"""Instruction-tune a small open model with LoRA / QLoRA (PEFT). Free: HuggingFace weights, local GPU/CPU.

    pip install -e ".[finetune]"
    python scripts/finetune_lora.py --data data/instructions.jsonl --out var/lora --qlora

Data format (JSONL): {"instruction": "...", "input": "...", "output": "..."}
"""
from __future__ import annotations

import argparse
import json


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="TinyLlama/TinyLlama-1.1B-Chat-v1.0")
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="var/lora")
    ap.add_argument("--qlora", action="store_true", help="4-bit quantised base (needs CUDA + bitsandbytes)")
    ap.add_argument("--epochs", type=int, default=2)
    args = ap.parse_args()

    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig,
                              DataCollatorForLanguageModeling, Trainer, TrainingArguments)

    tok = AutoTokenizer.from_pretrained(args.base)
    tok.pad_token = tok.pad_token or tok.eos_token
    kwargs = {}
    if args.qlora:
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(args.base, **kwargs)
    if args.qlora:
        model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05,
                                             target_modules=["q_proj", "v_proj"], task_type="CAUSAL_LM"))
    model.print_trainable_parameters()

    rows = [json.loads(l) for l in open(args.data, encoding="utf-8") if l.strip()]
    texts = [f"### Instruction:\n{r['instruction']}\n### Input:\n{r.get('input', '')}\n### Response:\n{r['output']}"
             for r in rows]
    ds = Dataset.from_dict({"text": texts}).map(
        lambda b: tok(b["text"], truncation=True, max_length=512), batched=True, remove_columns=["text"])
    Trainer(model=model,
            args=TrainingArguments(args.out, num_train_epochs=args.epochs, per_device_train_batch_size=2,
                                   gradient_accumulation_steps=4, learning_rate=2e-4, logging_steps=10,
                                   report_to="none"),
            train_dataset=ds,
            data_collator=DataCollatorForLanguageModeling(tok, mlm=False)).train()
    model.save_pretrained(args.out)
    print("adapter saved to", args.out)


if __name__ == "__main__":
    main()
