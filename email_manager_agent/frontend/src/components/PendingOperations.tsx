import type { Decision, PendingOperation } from "../types/api";
import { OperationCard } from "./OperationCard";

interface Props {
  pending: PendingOperation[];
  opBusy: Record<string, boolean>;
  opErrors: Record<string, string>;
  onDecide: (operationId: string, decision: Decision, acknowledge: boolean) => void;
  onExpire: (operationId: string) => void;
}

export function PendingOperations({ pending, opBusy, opErrors, onDecide, onExpire }: Props) {
  if (pending.length === 0) return null;
  return (
    <section className="pending" aria-labelledby="pending-title">
      <h2 id="pending-title">Waiting for your approval</h2>
      <p className="pending-hint">Approve runs exactly what's shown. Reject cancels it.</p>
      {pending.map((op) => (
        <OperationCard
          key={op.operation_id}
          op={op}
          busy={!!opBusy[op.operation_id]}
          error={opErrors[op.operation_id]}
          onDecide={onDecide}
          onExpire={onExpire}
        />
      ))}
    </section>
  );
}
