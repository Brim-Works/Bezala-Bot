/* Route-mappning — används av både Sidebar (navigering) och App-skalet
 * (matcha path → vy). Hålls i en liten separat fil för att undvika
 * cirkulära imports mellan Sidebar och App. */

export const ROUTES = {
  dashboard: '/',
  review: '/review',
  match: '/match',
  travelTinder: '/travel-tinder',
  trips: '/trips',
  log: '/log',
  matchHealth: '/match-health',
  settings: '/settings',
  trash: '/trash',
};

export function routeForView(view) {
  return ROUTES[view] || ROUTES.dashboard;
}

export function viewForPath(path) {
  switch (path) {
    case '/':
      return 'dashboard';
    case '/review':
      return 'review';
    case '/match':
      return 'match';
    case '/travel-tinder':
      return 'travelTinder';
    case '/trips':
      return 'trips';
    case '/log':
      return 'log';
    case '/match-health':
      return 'matchHealth';
    case '/settings':
      return 'settings';
    case '/trash':
      return 'trash';
    default:
      return null;
  }
}
