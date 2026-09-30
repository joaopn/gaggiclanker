# Constructed profiles

These profiles are **constructed**, not exported from a machine. The profiles the four real fixture shots ran (`Amigo Alturas Classic [AI]`, `Amigo Alturas Bloom [AI]`, `El Girasol Bloom [AI]`, `Gratus 16:32 trad`) no longer exist, and the shots' `.slog` header does not carry the pump mode of each phase.

Each file was written to match one real shot: the number, order and names of its phases come from the shot's transition table, and each phase's pump mode was inferred from the shapes of the logged targets (`tp`, `tf`): every brew phase steers by pressure with a flow limit, a bloom phase logging 0/0 is a simple (integer) pump phase, and the first phase, which logs a pressure and a flow target together, is ambiguous. So each shot has two variants:

- `<shot>_pressure-first.json`: the first phase steers by pressure and the flow is its limit;
- `<shot>_flow-first.json`: the first phase steers by flow and the pressure is its limit.

Durations, temperatures and stop conditions are plausible values, not the machine's. Tests use them to derive the real shots with a profile behind them; never treat one as what a shot actually ran.
