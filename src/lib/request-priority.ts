export class LatestForegroundRequestGate {
  private revision = 0;
  private foreground = 0;
  private latestAuthoritative = 0;

  beginForeground(): number {
    const token = ++this.revision;
    this.foreground = token;
    return token;
  }

  beginAuthoritative(): number {
    const token = this.beginForeground();
    this.latestAuthoritative = token;
    return token;
  }

  commitAuthoritative(token: number): number | null {
    if (token !== this.latestAuthoritative) {
      return null;
    }
    return ++this.revision;
  }

  endForeground(token: number): void {
    if (this.foreground === token) {
      this.foreground = 0;
    }
  }

  beginBackground(): number | null {
    if (this.foreground !== 0) {
      return null;
    }
    return ++this.revision;
  }

  isCurrent(token: number): boolean {
    return token === this.revision;
  }
}
