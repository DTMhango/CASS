import { useState } from "react";

import { ApiError } from "@/api/client";
import { useSignIn } from "@/api/hooks";
import { Button, Field, Notice, PasswordInput, TextInput } from "@/components/primitives";
import logoBox from "@/assets/logo_box.png";

import "./SignIn.css";

export function SignIn() {
  const signIn = useSignIn();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const error = signIn.error as ApiError | null;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    signIn.mutate({ username, password });
  }

  return (
    <div className="signin">
      <form className="signin__panel" onSubmit={submit}>
        <div className="signin__brand">
          <img className="signin__logo" src={logoBox} alt="Klapton Re" />
          <div>
            <h1 className="signin__title">CASS</h1>
            <p className="signin__subtitle">Catastrophe Analytics and Scenario Suite.</p>
          </div>
        </div>

        {error ? (
          <Notice tone="error" title="Sign-in failed">
            {error.message}
          </Notice>
        ) : null}

        <Field label="Username" htmlFor="username" required>
          <TextInput
            id="username"
            name="username"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
          />
        </Field>

        <Field label="Password" htmlFor="password" required>
          <PasswordInput
            id="password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </Field>

        <Button type="submit" variant="primary" busy={signIn.isPending}>
          Sign in
        </Button>

        <p className="signin__footnote">
          Access is recorded. Portfolio and model data are confidential to Klapton Re.
        </p>
      </form>
    </div>
  );
}
