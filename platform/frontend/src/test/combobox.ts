/**
 * Choosing from a combobox in a test, the way a person does: open it, then
 * pick the option by what it says rather than by a value nobody sees.
 */

import { screen } from "@testing-library/react";
import type userEvent from "@testing-library/user-event";

export async function choose(
  user: ReturnType<typeof userEvent.setup>,
  combobox: HTMLElement,
  option: string | RegExp,
) {
  await user.click(combobox);
  await user.click(await screen.findByRole("option", { name: option }));
}
