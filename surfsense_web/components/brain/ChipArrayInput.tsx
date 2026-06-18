"use client";

/**
 * @file ChipArrayInput.tsx
 * @module components/brain
 *
 * Input interactivo para arrays de strings (tags, aliases, extensiones).
 * El usuario escribe y pulsa Enter o coma para añadir un chip.
 * Cada chip tiene botón de eliminación.
 *
 * Usado en: Vocabulario (aliases), Dominios (signal_tags, signal_kw),
 *           Tipos de doc (signal_formats), Entity Hints (patterns, examples).
 *
 * Gestión de logs: no — componente puramente presentacional sin efectos async.
 * ZERO HARDCODE: regex de validación se recibe como prop opcional.
 */

import { useState, useRef, KeyboardEvent } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface ChipArrayInputProps {
  /** Valores actuales del array */
  value: string[];
  /** Callback al modificar el array */
  onChange: (newValue: string[]) => void;
  /** Placeholder del input */
  placeholder?: string;
  /** Si true, el componente no acepta interacción */
  disabled?: boolean;
  /** Regex de validación por chip; si no valida, el chip se muestra en rojo */
  validatePattern?: RegExp;
  /** Mensaje de error de validación */
  validationError?: string;
  /** Clases CSS adicionales para el contenedor */
  className?: string;
}

export function ChipArrayInput({
  value,
  onChange,
  placeholder = "Añadir… (Enter o coma)",
  disabled = false,
  validatePattern,
  validationError,
  className,
}: ChipArrayInputProps) {
  const [inputValue, setInputValue] = useState("");
  const [validationMsg, setValidationMsg] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const addChip = (raw: string) => {
    const trimmed = raw.trim().toLowerCase().replace(/,+$/, "");
    if (!trimmed) return;
    if (value.includes(trimmed)) {
      setValidationMsg("Ya existe este valor");
      return;
    }
    if (validatePattern && !validatePattern.test(trimmed)) {
      setValidationMsg(validationError ?? "Formato inválido");
      return;
    }
    setValidationMsg(null);
    onChange([...value, trimmed]);
    setInputValue("");
  };

  const removeChip = (chip: string) => {
    if (disabled) return;
    onChange(value.filter((v) => v !== chip));
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addChip(inputValue);
    } else if (e.key === "Backspace" && !inputValue && value.length > 0) {
      // Backspace con input vacío → eliminar último chip
      removeChip(value[value.length - 1]);
    }
  };

  const handleBlur = () => {
    if (inputValue.trim()) {
      addChip(inputValue);
    }
  };

  return (
    <div className={cn("space-y-1.5", className)}>
      <div
        className={cn(
          "flex flex-wrap gap-1.5 min-h-[40px] w-full rounded-md border border-slate-700 bg-slate-900/50 px-3 py-2",
          "focus-within:border-violet-500 transition-colors",
          disabled && "opacity-50 cursor-not-allowed",
        )}
        onClick={() => !disabled && inputRef.current?.focus()}
      >
        {value.map((chip) => {
          const isInvalid = validatePattern && !validatePattern.test(chip);
          return (
            <span
              key={chip}
              className={cn(
                "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium",
                isInvalid
                  ? "bg-red-500/15 text-red-300 border border-red-500/30"
                  : "bg-violet-500/15 text-violet-300 border border-violet-500/20",
              )}
            >
              {chip}
              {!disabled && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    removeChip(chip);
                  }}
                  className="hover:text-white transition-colors ml-0.5"
                  aria-label={`Eliminar ${chip}`}
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </span>
          );
        })}

        {!disabled && (
          <input
            ref={inputRef}
            type="text"
            value={inputValue}
            onChange={(e) => {
              setInputValue(e.target.value);
              setValidationMsg(null);
            }}
            onKeyDown={handleKeyDown}
            onBlur={handleBlur}
            placeholder={value.length === 0 ? placeholder : ""}
            className="flex-1 min-w-24 bg-transparent text-xs text-slate-200 outline-none placeholder:text-slate-600"
          />
        )}
      </div>

      {validationMsg && (
        <p className="text-xs text-red-400">{validationMsg}</p>
      )}
    </div>
  );
}
