import { useState } from "react";
import type { Control, FieldErrors } from "react-hook-form";
import { toast } from "sonner";
import type { ResolvedSetting } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuthStatus, useSetPassword } from "@/hooks/useAuth";
import { SettingField } from "@/pages/settings/SettingField";
import { isEditable, type SettingsFormValues } from "@/pages/settings/schema";

/** Below this the box is not really locked. Matches the server's own rule. */
const MIN_PASSWORD_LENGTH = 12;

/**
 * The auth corner of the Settings page.
 *
 * `authUser` and `authTokenTtlSeconds` are ordinary generated fields. The
 * password is not, and that is the whole point of this component existing:
 * `authPasswordHash` used to render as a masked box labelled "Auth password
 * hash", which invited typing the *password* into it — and a stored value
 * argon2 cannot verify is a credential that authenticates nobody. The server
 * now refuses that key through `PATCH /api/settings` entirely; here it shows
 * only whether a password is set, with a control that posts the plain password
 * to `POST /api/auth/password` and lets the server do the hashing.
 */
export function AuthSection({
  entries,
  control,
  errors,
  disabled,
}: {
  entries: ResolvedSetting[];
  control: Control<SettingsFormValues>;
  errors: FieldErrors<SettingsFormValues>;
  disabled?: boolean;
}) {
  const status = useAuthStatus();
  const setPassword = useSetPassword();

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  const hash = entries.find((entry) => entry.key === "authPasswordHash");
  const configured = hash?.secret === true && hash.configured;
  const authRequired = status.data?.auth_required === true;

  const submit = async () => {
    if (next.length < MIN_PASSWORD_LENGTH) {
      setProblem(`At least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (next !== confirm) {
      setProblem("The two entries do not match.");
      return;
    }
    setProblem(null);
    try {
      const result = await setPassword.mutateAsync({
        ...(configured ? { currentPassword: current } : {}),
        newPassword: next,
      });
      setCurrent("");
      setNext("");
      setConfirm("");
      toast.success(
        result.auth_required
          ? "Password changed. Every session was signed out, including this one."
          : "Password set. Authentication turns on once a username is set too.",
      );
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "Could not set the password");
    }
  };

  return (
    <>
      {entries.filter(isEditable).map((setting) => (
        <SettingField
          key={setting.key}
          setting={setting}
          control={control}
          error={errors[setting.key]}
          disabled={disabled}
        />
      ))}

      <div className="space-y-3 border-border border-t pt-5">
        <div className="flex flex-wrap items-center gap-2">
          <Label htmlFor="auth-new-password">Sign-in password</Label>
          <Badge variant="secondary" className="font-normal text-[10px]">
            {configured ? "set" : "not set"}
          </Badge>
          {authRequired ? (
            <Badge variant="outline" className="font-normal text-[10px]">
              authentication is on
            </Badge>
          ) : null}
        </div>

        {configured ? (
          <div className="space-y-1.5">
            <Label htmlFor="auth-current-password" className="text-muted-foreground text-xs">
              Current password
            </Label>
            <Input
              id="auth-current-password"
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(event) => setCurrent(event.target.value)}
              disabled={disabled || setPassword.isPending}
            />
          </div>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="auth-new-password" className="text-muted-foreground text-xs">
              New password
            </Label>
            <Input
              id="auth-new-password"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(event) => setNext(event.target.value)}
              disabled={disabled || setPassword.isPending}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="auth-confirm-password" className="text-muted-foreground text-xs">
              Repeat it
            </Label>
            <Input
              id="auth-confirm-password"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              disabled={disabled || setPassword.isPending}
            />
          </div>
        </div>

        {problem ? (
          <p role="alert" className="text-destructive text-xs">
            {problem}
          </p>
        ) : null}

        <Button
          // Not a submit: the surrounding form saves the settings registry, and
          // the password does not go through it. Pressing Enter in these boxes
          // must not send a half-typed password through `PATCH /api/settings`
          // either — which it cannot, because the key is not in the form.
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || setPassword.isPending || next.length === 0}
          onClick={() => void submit()}
        >
          {setPassword.isPending ? "Saving..." : configured ? "Change password" : "Set password"}
        </Button>

        <p className="text-muted-foreground text-xs">
          Hashed on the server with argon2id; the browser never holds the hash and the plain
          password is never stored. Changing it signs out every open session, this one included.
          Authentication is on only once a username is set as well.
        </p>
      </div>
    </>
  );
}
