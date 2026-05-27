import { Button, Text, hubspot } from '@hubspot/ui-extensions';
import { CrmActionButton } from '@hubspot/ui-extensions/crm';
import { useState } from 'react';

interface HubSpotContext {
  crm?: {
    objectId?: number;
    objectTypeId?: string;
  };
  portal?: {
    id?: number;
  };
  user?: {
    id?: number;
    email?: string;
  };
}

interface DraftSource {
  title?: string;
  url?: string;
  label?: string;
}

interface DraftResponse {
  success: boolean;
  subject?: string;
  body?: string;
  contactId?: string;
  error?: string;
  sources?: DraftSource[];
  warnings?: string[];
}

interface FunctionResponse {
  statusCode?: number;
  body?: DraftResponse;
}

interface ServerlessEnvelope {
  status?: 'SUCCESS' | 'ERROR';
  response?: FunctionResponse | DraftResponse;
  message?: string;
}

hubspot.extend<'helpdesk.sidebar'>(({ context }) => (
  <CrmExtension context={context as HubSpotContext} />
));

const CrmExtension = ({ context }: { context: HubSpotContext }) => {
  const [draft, setDraft] = useState<DraftResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const ticketId = context.crm?.objectId;
  const portalId = context.portal?.id;

  const sourceSummary =
    draft?.sources
      ?.map((source) => source.title || source.url || source.label)
      .filter(Boolean)
      .join(', ') || '';

  const generateDraft = async () => {
    if (!ticketId) {
      setError('HubSpot gaf geen ticket-ID door.');
      return;
    }

    setLoading(true);
    setError('');
    setDraft(null);

    try {
      const result = await hubspot.serverless<
        ServerlessEnvelope | FunctionResponse
      >('klai_email_support_app_function', {
        parameters: {
          ticketId,
          portalId,
        },
        propertiesToSend: ['subject', 'content'],
      });
      const body = normalizeServerlessResult(result);

      if (!body.success) {
        setError(body.error || 'Klai kon geen concept maken.');
        return;
      }

      setDraft(body);
    } catch (caughtError) {
      setError(
        caughtError instanceof Error
          ? caughtError.message
          : 'De HubSpot serverless function gaf een fout terug.',
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <Text format={{ fontWeight: 'bold' }}>Klai concept reply</Text>
      <Text>
        Portal: {portalId ?? 'unknown'}, ticket: {ticketId ?? 'unknown'}.
      </Text>

      <Button onClick={generateDraft} disabled={!ticketId || loading}>
        {loading ? 'Draft maken...' : 'Draft concept reply'}
      </Button>

      {error ? <Text>{error}</Text> : null}

      {draft?.warnings?.length ? <Text>{draft.warnings.join(' ')}</Text> : null}

      {draft?.body ? (
        <>
          <Text format={{ fontWeight: 'bold' }}>Preview</Text>
          <Text>{draft.body}</Text>
          {sourceSummary ? <Text>Bronnen: {sourceSummary}</Text> : null}
          {draft.contactId ? (
            <CrmActionButton
              actionType="SEND_EMAIL"
              actionContext={{
                objectTypeId: '0-1',
                objectId: Number(draft.contactId),
                initialEmailSubject: draft.subject || 'Re: supportvraag',
                initialEmailBody: draft.body,
              }}
              variant="secondary"
            >
              Open e-mailconcept
            </CrmActionButton>
          ) : (
            <Text>
              Geen gekoppeld contact gevonden; HubSpot kan de e-mailcomposer nog
              niet openen.
            </Text>
          )}
        </>
      ) : null}
    </>
  );
};

function normalizeServerlessResult(
  result: ServerlessEnvelope | FunctionResponse,
): DraftResponse {
  if ('status' in result && result.status === 'ERROR') {
    return {
      success: false,
      error: result.message || 'HubSpot serverless execution failed.',
    };
  }

  const payload =
    'response' in result && result.response ? result.response : result;
  if ('body' in payload && payload.body) {
    return payload.body;
  }

  return payload as DraftResponse;
}
