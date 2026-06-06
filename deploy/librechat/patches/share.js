const mongoose = require('mongoose');
const express = require('express');
const { isEnabled, isActiveExpirationDate, getSharedLinkExpiration } = require('@librechat/api');
const { logger, createTempChatExpirationDate } = require('@librechat/data-schemas');
const {
  getSharedMessages,
  createSharedLink,
  updateSharedLink,
  deleteSharedLink,
  getSharedLinks,
  getSharedLink,
} = require('~/models');
const requireJwtAuth = require('~/server/middleware/requireJwtAuth');
const router = express.Router();

const resolveSharedLinkExpiration = (req, conversationId) =>
  getSharedLinkExpiration(
    { req, conversationId },
    {
      getConvo: async (userId, sourceConversationId) => {
        const Conversation = mongoose.models.Conversation;
        return Conversation.findOne(
          { conversationId: sourceConversationId, user: userId },
          'isTemporary expiredAt',
        ).lean();
      },
      createExpirationDate: createTempChatExpirationDate,
      logger,
    },
  );

const PLACEHOLDER_HOSTS = new Set([
  'example.com',
  'example.org',
  'example.net',
  'localhost',
  'test.com',
  'test.nl',
]);

function parseUrl(value) {
  try {
    return new URL(value, process.env.DOMAIN_CLIENT || 'https://chat.getklai.com');
  } catch {
    return null;
  }
}

function isPlaceholderUrl(value) {
  const parsed = parseUrl(value);
  if (!parsed) {
    return false;
  }
  const host = parsed.hostname.toLowerCase();
  return PLACEHOLDER_HOSTS.has(host) || host.endsWith('.example.com');
}

function isAllowedImageUrl(value) {
  const parsed = parseUrl(value);
  if (!parsed) {
    return false;
  }
  const host = parsed.hostname.toLowerCase();
  const ownHost = parseUrl(process.env.DOMAIN_CLIENT || '')?.hostname?.toLowerCase();
  const isOwnHost = ownHost && host === ownHost;
  const isKlaiHost = host === 'getklai.com' || host.endsWith('.getklai.com');
  return (
    (isOwnHost || isKlaiHost) &&
    (parsed.pathname.startsWith('/kb-images/') ||
      parsed.pathname.startsWith('/images/') ||
      parsed.pathname.startsWith('/api/files/'))
  );
}

function sanitizeMarkdown(text) {
  if (typeof text !== 'string' || text.length === 0) {
    return text;
  }
  return text
    .replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)/g, (_match, alt, url) => {
      if (isAllowedImageUrl(url)) {
        return _match;
      }
      return '';
    })
    .replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)/g, (_match, label, url) => {
      if (isPlaceholderUrl(url)) {
        return String(label);
      }
      return _match;
    })
    .replace(/https?:\/\/[^\s<>)]+/g, (url) => (isPlaceholderUrl(url) ? '' : url));
}

function sanitizeMessage(message) {
  if (!message || typeof message !== 'object') {
    return message;
  }
  if (typeof message.text === 'string') {
    message.text = sanitizeMarkdown(message.text);
  }
  if (Array.isArray(message.content)) {
    for (const part of message.content) {
      if (part && typeof part === 'object' && typeof part.text === 'string') {
        part.text = sanitizeMarkdown(part.text);
      }
    }
  }
  return message;
}

function sanitizeShare(share) {
  if (share && Array.isArray(share.messages)) {
    share.messages = share.messages.map(sanitizeMessage);
  }
  return share;
}

/**
 * Shared messages
 */
const allowSharedLinks =
  process.env.ALLOW_SHARED_LINKS === undefined || isEnabled(process.env.ALLOW_SHARED_LINKS);

if (allowSharedLinks) {
  const allowSharedLinksPublic = isEnabled(process.env.ALLOW_SHARED_LINKS_PUBLIC);
  router.get(
    '/:shareId',
    allowSharedLinksPublic ? (req, res, next) => next() : requireJwtAuth,
    async (req, res) => {
      try {
        const share = await getSharedMessages(req.params.shareId);

        if (share) {
          res.status(200).json(sanitizeShare(share));
        } else {
          res.status(404).end();
        }
      } catch (error) {
        logger.error('Error getting shared messages:', error);
        res.status(500).json({ message: 'Error getting shared messages' });
      }
    },
  );
}

/**
 * Shared links
 */
router.get('/', requireJwtAuth, async (req, res) => {
  try {
    const params = {
      pageParam: req.query.cursor,
      pageSize: Math.max(1, parseInt(req.query.pageSize) || 10),
      isPublic: isEnabled(req.query.isPublic),
      sortBy: ['createdAt', 'title'].includes(req.query.sortBy) ? req.query.sortBy : 'createdAt',
      sortDirection: ['asc', 'desc'].includes(req.query.sortDirection)
        ? req.query.sortDirection
        : 'desc',
      search: req.query.search ? decodeURIComponent(req.query.search.trim()) : undefined,
    };

    const result = await getSharedLinks(
      req.user.id,
      params.pageParam,
      params.pageSize,
      params.isPublic,
      params.sortBy,
      params.sortDirection,
      params.search,
    );

    res.status(200).send({
      links: result.links,
      nextCursor: result.nextCursor,
      hasNextPage: result.hasNextPage,
    });
  } catch (error) {
    logger.error('Error getting shared links:', error);
    res.status(500).json({
      message: 'Error getting shared links',
      error: error.message,
    });
  }
});

router.get('/link/:conversationId', requireJwtAuth, async (req, res) => {
  try {
    const share = await getSharedLink(req.user.id, req.params.conversationId);

    return res.status(200).json({
      success: share.success,
      shareId: share.shareId,
      targetMessageId: share.targetMessageId,
      conversationId: req.params.conversationId,
    });
  } catch (error) {
    logger.error('Error getting shared link:', error);
    res.status(500).json({ message: 'Error getting shared link' });
  }
});

router.post('/:conversationId', requireJwtAuth, async (req, res) => {
  try {
    const { targetMessageId } = req.body;
    const expiredAt = await resolveSharedLinkExpiration(req, req.params.conversationId);
    if (expiredAt != null && !isActiveExpirationDate(expiredAt)) {
      return res.status(404).end();
    }

    const created = await createSharedLink(
      req.user.id,
      req.params.conversationId,
      targetMessageId,
      expiredAt,
    );
    if (created) {
      res.status(200).json(created);
    } else {
      res.status(404).end();
    }
  } catch (error) {
    logger.error('Error creating shared link:', error);
    res.status(500).json({ message: 'Error creating shared link' });
  }
});

router.patch('/:shareId', requireJwtAuth, async (req, res) => {
  try {
    const { targetMessageId } = req.body ?? {};
    if (targetMessageId !== undefined && typeof targetMessageId !== 'string') {
      return res.status(400).json({ message: 'targetMessageId must be a string' });
    }

    let expiredAt;
    const SharedLink = mongoose.models.SharedLink;
    const existing = await SharedLink.findOne(
      { shareId: req.params.shareId, user: req.user.id },
      'conversationId',
    ).lean();
    if (existing?.conversationId) {
      expiredAt = await resolveSharedLinkExpiration(req, existing.conversationId);
    }
    if (expiredAt != null && !isActiveExpirationDate(expiredAt)) {
      return res.status(404).end();
    }

    const updatedShare = await updateSharedLink(
      req.user.id,
      req.params.shareId,
      targetMessageId,
      expiredAt,
    );
    if (updatedShare) {
      res.status(200).json(updatedShare);
    } else {
      res.status(404).end();
    }
  } catch (error) {
    logger.error('Error updating shared link:', error);
    res.status(500).json({ message: 'Error updating shared link' });
  }
});

router.delete('/:shareId', requireJwtAuth, async (req, res) => {
  try {
    const result = await deleteSharedLink(req.user.id, req.params.shareId);
    if (result) {
      res.status(200).json({ message: 'Shared link deleted successfully' });
    } else {
      res.status(404).end();
    }
  } catch (error) {
    logger.error('Error deleting shared link:', error);
    res.status(500).json({ message: 'Error deleting shared link' });
  }
});

module.exports = router;
