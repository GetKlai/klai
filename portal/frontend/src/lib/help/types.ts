export interface HelpStep {
  /** Matches data-help-id attribute on the target element */
  id: string
  title: () => string
  description: () => string
}

export interface HelpPageIntro {
  title: () => string
  description: () => string
}
