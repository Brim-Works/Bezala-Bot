import { Component } from 'react';

/* Per-vy error-boundary. Använder react-context vid behov — nu bara
 * en enkel klasskomponent med i18n-fallback via props. */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('ErrorBoundary caught:', error, info);
  }

  reset = () => {
    this.setState({ error: null });
    if (typeof window !== 'undefined') {
      window.location.reload();
    }
  };

  render() {
    const { error } = this.state;
    const { children, fallback } = this.props;
    if (!error) return children;
    if (fallback) return fallback({ error, reset: this.reset });
    return (
      <div className="error-boundary" data-testid="error-boundary">
        <h2 className="error-boundary__title">Något gick fel</h2>
        <p className="muted">{error.message || String(error)}</p>
        <button type="button" className="btn" onClick={this.reset}>
          Ladda om
        </button>
      </div>
    );
  }
}
