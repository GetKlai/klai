import { createFileRoute } from '@tanstack/react-router'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { EMPTY_INSTRUCTION_FORM, InstructionFormPage } from './-instruction-form'

export const Route = createFileRoute('/app/instructions/new')({
  component: () => (
    <ProductGuard product="chat">
      <InstructionFormPage mode="new" initialForm={EMPTY_INSTRUCTION_FORM} />
    </ProductGuard>
  ),
})
