/**
 * Observation Embedder
 *
 * Embeds observations into the memory vector index using the same
 * embedding model as the code graph (snowflake-arctic-embed-xs, 384-dim).
 *
 * Called after createObservation() to enable semantic recall.
 */

import { initEmbedder, embedText, isEmbedderReady, embeddingToArray } from '../embeddings/embedder.js';
import { memoryQuery } from './memory-adapter.js';

/**
 * Embed a single observation and store in MemoryEmbedding table.
 * Text is: "{name}. {content}" for best retrieval.
 */
export async function embedObservation(
  dbKey: string,
  uid: string,
  name: string,
  content: string,
): Promise<void> {
  // Initialize embedder if not ready (lazy load)
  if (!isEmbedderReady()) {
    await initEmbedder();
  }

  const text = `${name}. ${content}`;
  const embedding = await embedText(text);
  const arr = embeddingToArray(embedding);

  // Upsert into MemoryEmbedding table
  // First try to delete existing (for updates)
  try {
    await memoryQuery(dbKey, `
      MATCH (e:MemoryEmbedding {nodeId: '${uid}'})
      DELETE e
    `);
  } catch {}

  await memoryQuery(dbKey, `
    CREATE (e:MemoryEmbedding {
      nodeId: '${uid}',
      embedding: [${arr.join(',')}]
    })
  `);
}

/**
 * Semantic search over observations using vector similarity.
 * Returns UIDs sorted by cosine similarity.
 */
export async function semanticSearchObservations(
  dbKey: string,
  query: string,
  limit: number = 10,
): Promise<Array<{ uid: string; score: number }>> {
  if (!isEmbedderReady()) {
    await initEmbedder();
  }

  const embedding = await embedText(query);
  const arr = embeddingToArray(embedding);

  try {
    const rows = await memoryQuery(dbKey, `
      CALL vector_search('MemoryEmbedding', 'memory_embedding_idx', [${arr.join(',')}], ${limit})
      RETURN node.nodeId AS uid, distance AS score
    `);
    return rows.map((r: any) => ({ uid: r.uid, score: r.score }));
  } catch {
    // Vector index may not exist yet (no observations embedded)
    return [];
  }
}

/**
 * Delete embedding for an observation.
 */
export async function deleteObservationEmbedding(
  dbKey: string,
  uid: string,
): Promise<void> {
  try {
    await memoryQuery(dbKey, `
      MATCH (e:MemoryEmbedding {nodeId: '${uid}'})
      DELETE e
    `);
  } catch {}
}
