import {
  Button,
  Link,
  List,
  Text,
  TextArea,
  hubspot,
} from '@hubspot/ui-extensions';
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
  supportSessionId?: string;
  error?: string;
  sources?: DraftSource[];
  customerLinks?: DraftSource[];
  colleagueLinks?: DraftSource[];
  internalSources?: DraftSource[];
  lowMatchSources?: DraftSource[];
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

interface ChatEntry {
  role: 'Jij' | 'Klai';
  content: string;
}

hubspot.extend<'helpdesk.sidebar'>(({ context }) => (
  <CrmExtension context={context as HubSpotContext} />
));

const CrmExtension = ({ context }: { context: HubSpotContext }) => {
  const [draft, setDraft] = useState<DraftResponse | null>(null);
  const [error, setError] = useState('');
  const [instruction, setInstruction] = useState('');
  const [conversation, setConversation] = useState<ChatEntry[]>([]);
  const [loading, setLoading] = useState(false);

  const ticketId = context.crm?.objectId;
  const portalId = context.portal?.id;

  const baseParameters = (): Record<string, number | string> => {
    const parameters: Record<string, number | string> = {};
    if (ticketId) {
      parameters.ticketId = ticketId;
    }
    if (portalId) {
      parameters.portalId = portalId;
    }
    if (context.user?.id) {
      parameters.hubspotUserId = context.user.id;
    }
    if (draft?.supportSessionId) {
      parameters.supportSessionId = draft.supportSessionId;
    }
    return parameters;
  };

  const callDraftFunction = async (
    parameters: Record<string, number | string>,
  ): Promise<DraftResponse> => {
    const result = await hubspot.serverless<
      ServerlessEnvelope | FunctionResponse
    >('klai_email_support_app_function', {
      parameters,
      propertiesToSend: ['subject', 'content'],
    });

    return normalizeServerlessResult(result);
  };

  const generateDraft = async () => {
    if (!ticketId) {
      setError('HubSpot gaf geen ticket-ID door.');
      return;
    }

    setLoading(true);
    setError('');
    setDraft(null);
    setConversation([]);

    try {
      const body = await callDraftFunction({
        ...baseParameters(),
        mode: 'generate',
      });

      if (!body.success) {
        setError(body.error || 'Klai kon geen concept maken.');
        return;
      }

      setDraft(body);
      if (body.body) {
        setConversation([{ role: 'Klai', content: body.body }]);
      }
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

  const reviseDraft = async (requestedInstruction = instruction) => {
    const cleanInstruction = requestedInstruction.trim();
    if (!ticketId) {
      setError('HubSpot gaf geen ticket-ID door.');
      return;
    }
    if (!draft?.body) {
      setError('Maak eerst een concept voordat je kunt sparren.');
      return;
    }
    if (!cleanInstruction) {
      setError('Geef eerst aan wat Klai moet aanpassen.');
      return;
    }

    setLoading(true);
    setError('');

    const userEntry: ChatEntry = {
      role: 'Jij',
      content: cleanInstruction,
    };
    const nextConversation = [...conversation, userEntry].slice(-8);

    try {
      const body = await callDraftFunction({
        ...baseParameters(),
        mode: 'revise',
        instruction: cleanInstruction,
        currentDraftBody: draft.body,
        conversationHistory: JSON.stringify(nextConversation),
      });

      if (!body.success) {
        setError(body.error || 'Klai kon het concept niet aanpassen.');
        return;
      }

      setDraft(body);
      setConversation(
        [
          ...nextConversation,
          { role: 'Klai' as const, content: body.body || '' },
        ]
          .filter((entry) => entry.content)
          .slice(-8),
      );
      setInstruction('');
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
      {draft?.supportSessionId ? (
        <Text>Sessie: {draft.supportSessionId}</Text>
      ) : null}

      <Button onClick={generateDraft} disabled={!ticketId || loading}>
        {loading ? 'Draft maken...' : 'Draft concept reply'}
      </Button>

      {error ? <Text>{error}</Text> : null}

      {draft?.warnings?.length ? <Text>{draft.warnings.join(' ')}</Text> : null}

      {draft?.body ? (
        <>
          <Text format={{ fontWeight: 'bold' }}>Conceptmail</Text>
          <Text>{draft.body}</Text>
          <SourceLinks
            title="Links voor klant"
            sources={draft.customerLinks}
            emptyText=""
          />
          <SourceLinks
            title="Relevante links voor collega"
            sources={draft.colleagueLinks}
            emptyText="Geen publieke bronlinks gevonden voor deze vraag."
          />
          <SourceTitles
            title="Interne bronnen zonder publieke link"
            sources={draft.internalSources}
          />
          <SourceTitles
            title="Mogelijk minder relevant"
            sources={draft.lowMatchSources}
          />
          <Text format={{ fontWeight: 'bold' }}>Spar met Klai</Text>
          <TextArea
            label="Aanpassing"
            name="klai_revision_instruction"
            value={instruction}
            rows={3}
            resize="vertical"
            placeholder="Bijvoorbeeld: maak korter, laat Zoom Phone weg, voeg een vervolgvraag toe..."
            onInput={setInstruction}
          />
          <Button
            onClick={() => reviseDraft()}
            disabled={!draft.body || !instruction.trim() || loading}
          >
            {loading ? 'Klai denkt mee...' : 'Pas concept aan'}
          </Button>
          <List variant="inline-divided">
            <Button
              size="xs"
              variant="secondary"
              onClick={() => reviseDraft('Maak het concept korter.')}
              disabled={!draft.body || loading}
            >
              Korter
            </Button>
            <Button
              size="xs"
              variant="secondary"
              onClick={() =>
                reviseDraft(
                  'Maak het concept voorzichtiger en vraag om bevestiging van ontbrekende details.',
                )
              }
              disabled={!draft.body || loading}
            >
              Voorzichtiger
            </Button>
            <Button
              size="xs"
              variant="secondary"
              onClick={() =>
                reviseDraft(
                  'Verwijder informatie die niet direct uit de klantvraag of relevante bronnen blijkt.',
                )
              }
              disabled={!draft.body || loading}
            >
              Strakker bij bron
            </Button>
          </List>
          <ConversationHistory entries={conversation} />
          {draft.contactId ? (
            <CrmActionButton
              actionType="SEND_EMAIL"
              actionContext={{
                objectTypeId: '0-1',
                objectId: Number(draft.contactId),
                initialEmailSubject: draft.subject || 'Re: supportvraag',
                initialEmailBody: buildEmailComposerBody(draft),
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

const SourceLinks = ({
  title,
  sources,
  emptyText,
}: {
  title: string;
  sources?: DraftSource[];
  emptyText: string;
}) => {
  const publicSources = (sources || []).filter((source) => source.url);

  if (!publicSources.length) {
    return emptyText ? <Text>{emptyText}</Text> : null;
  }

  return (
    <>
      <Text format={{ fontWeight: 'bold' }}>{title}</Text>
      <List variant="unordered-styled">
        {publicSources.map((source, index) => (
          <Link
            key={`${source.url}-${index}`}
            href={{ url: source.url || '', external: true }}
          >
            {source.title || source.url || `Bron ${index + 1}`}
          </Link>
        ))}
      </List>
    </>
  );
};

const SourceTitles = ({
  title,
  sources,
}: {
  title: string;
  sources?: DraftSource[];
}) => {
  const titles = (sources || [])
    .map((source) => source.title || source.label || source.url)
    .filter(Boolean);

  if (!titles.length) {
    return null;
  }

  return (
    <>
      <Text format={{ fontWeight: 'bold' }}>{title}</Text>
      <List variant="unordered-styled">
        {titles.map((sourceTitle, index) => (
          <Text key={`${sourceTitle}-${index}`}>{sourceTitle}</Text>
        ))}
      </List>
    </>
  );
};

const ConversationHistory = ({ entries }: { entries: ChatEntry[] }) => {
  if (entries.length <= 1) {
    return null;
  }

  return (
    <>
      <Text format={{ fontWeight: 'bold' }}>Sparringhistorie</Text>
      <List variant="unordered-styled">
        {entries.slice(-6).map((entry, index) => (
          <Text key={`${entry.role}-${index}`}>
            {entry.role}: {entry.content}
          </Text>
        ))}
      </List>
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
  const maybeFunctionResponse = payload as FunctionResponse;
  if (
    maybeFunctionResponse.body &&
    typeof maybeFunctionResponse.body === 'object'
  ) {
    return maybeFunctionResponse.body;
  }

  return payload as DraftResponse;
}

function buildEmailComposerBody(draft: DraftResponse): string {
  const body = draft.body || '';
  const links = (draft.customerLinks || [])
    .filter((source) => source.url && !body.includes(source.url))
    .map((source) => {
      const title = source.title || source.label || source.url;
      return `- ${title}: ${source.url}`;
    });

  if (!links.length) {
    return body;
  }

  return `${body}\n\nLinks:\n${links.join('\n')}`;
}
