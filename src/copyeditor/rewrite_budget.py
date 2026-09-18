from time import monotonic


class RewriteFailure(Exception):
    def __init__(self, code):
        self.code, self.field = code, None
        super().__init__(code)


class RewriteBudget:
    def __init__(self, meter, clock=monotonic):
        self.meter, self.clock = meter, clock
        self.deadline = meter.started_at + 240
        self.reservations = []
        self.overrun = False
        self.terminal_code = None

    @property
    def remaining(self):
        return max(0, self.deadline - self.clock())

    def checkpoint(self, *, provider_error=None, finish=None, validation_error=None):
        if self.terminal_code is None:
            if self.clock() >= self.deadline:
                self.terminal_code = "provider_timeout"
            elif provider_error:
                self.terminal_code = provider_error
            elif finish is not None and finish != "stop":
                self.terminal_code = ("generation_truncated" if finish == "truncated" else
                                      "provider_error" if finish == "blocked" else "invalid_response")
            elif validation_error:
                self.terminal_code = validation_error
            elif self.overrun:
                self.terminal_code = "request_budget"
        if self.terminal_code is not None:
            raise RewriteFailure(self.terminal_code)

    def start_call(self, estimated_input, *, is_regeneration):
        self.checkpoint()
        if type(estimated_input) is not int or estimated_input < 0:
            self.checkpoint(provider_error="provider_error")
        if type(is_regeneration) is not bool:
            raise ValueError("Regeneration marker must be boolean")
        reserved = ((5 * estimated_input + 3) // 4 + 1024, 8192)
        input_total = sum(value[0] for value in self.reservations) + reserved[0]
        output_total = sum(value[1] for value in self.reservations) + reserved[1]
        if input_total > 262144 or output_total > 139264:
            self.checkpoint(validation_error="request_budget")
        call = self.meter.start_call(is_regeneration=is_regeneration)
        self.reservations.append(reserved)
        return call

    def record_usage(self, call, usage):
        self.meter.record_usage(call, usage)
        reserved = self.reservations[call]
        # Recording an overrun must not bypass finish/integrity/size precedence.
        self.overrun |= any(actual is not None and actual > limit
                            for actual, limit in zip(usage[:2], reserved))
