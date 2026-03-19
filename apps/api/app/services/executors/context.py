from dataclasses import dataclass, field


@dataclass
class ExecutionContext:
    task_outputs: dict[str, dict] = field(default_factory=dict)

    def set_output(self, task_type: str, payload: dict) -> None:
        self.task_outputs[task_type] = payload

    def get_output(self, task_type: str) -> dict:
        return self.task_outputs.get(task_type, {})
