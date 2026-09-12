import { type Control, Controller, type FieldError } from "react-hook-form";
import type { ResolvedSetting } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { SettingsFormValues } from "@/pages/settings/schema";
import { humanizeKey } from "@/pages/settings/schema";

const SOURCE_LABEL: Record<string, string> = {
  database: "saved here",
  environment: "from the environment",
  default: "default",
};

/**
 * One registry entry, rendered from its declaration.
 *
 * The control is chosen by the declared type, not by the key, so a setting
 * added in a later chunk gets the right widget with no change on this side.
 */
export function SettingField({
  setting,
  control,
  error,
  disabled,
}: {
  setting: ResolvedSetting;
  control: Control<SettingsFormValues>;
  error?: FieldError;
  disabled?: boolean;
}) {
  const label = humanizeKey(setting.key);
  const id = `setting-${setting.key}`;

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor={id}>{label}</Label>
        <Badge variant="outline" className="font-normal text-[10px]">
          {SOURCE_LABEL[setting.source] ?? setting.source}
        </Badge>
        {setting.secret ? (
          <Badge variant="secondary" className="font-normal text-[10px]">
            {setting.configured ? `set - ${setting.hint}...` : "not set"}
          </Badge>
        ) : null}
      </div>

      <Controller
        name={setting.key}
        control={control}
        render={({ field }) => {
          if (setting.type === "bool") {
            return (
              <Select
                value={field.value ? "true" : "false"}
                onValueChange={(value) => field.onChange(value === "true")}
                disabled={disabled}
              >
                <SelectTrigger id={id} aria-label={label}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="true">Enabled</SelectItem>
                  <SelectItem value="false">Disabled</SelectItem>
                </SelectContent>
              </Select>
            );
          }
          return (
            <Input
              id={id}
              // A secret is write-only, so the box starts empty and masked:
              // there is nothing to reveal and nothing for a screen recording
              // to catch.
              type={setting.secret ? "password" : "text"}
              inputMode={setting.type === "int" || setting.type === "float" ? "numeric" : undefined}
              autoComplete={setting.secret ? "new-password" : "off"}
              placeholder={setting.secret ? "leave blank to keep" : undefined}
              value={typeof field.value === "string" ? field.value : ""}
              onChange={field.onChange}
              onBlur={field.onBlur}
              disabled={disabled}
              aria-invalid={error ? true : undefined}
            />
          );
        }}
      />

      <p className="text-muted-foreground text-xs">{setting.description}</p>
      {error ? <p className="text-destructive text-xs">{error.message}</p> : null}
    </div>
  );
}
