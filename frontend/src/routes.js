/* Route-mappning — används av både Sidebar (navigering) och App-skalet
 * (matcha path → vy). Hålls i en liten separat fil för att undvika
 * cirkulära imports mellan Sidebar och App. */

export const ROUTES = {
  dashboard: '/',
  travelTinder: '/travel-tinder',
  trips: '/trips',
  status: '/status',
  settings: '/settings',
  trash: '/trash',
};

/* C47 Del 6 — gamla URL:er pekar vidare till de sammanslagna sidorna. */
export const REDIRECTS = {
  '/review': '/travel-tinder?mode=review',
  '/match': '/travel-tinder?mode=match',
  '/log': '/status',
  '/match-health': '/status?tab=match-health',
};

export function routeForView(view) {
  return ROUTES[view] || ROUTES.dashboard;
}

export function viewForPath(path) {
  switch (path) {
    case '/':
      return 'dashboard';
    case '/travel-tinder':
      return 'travelTinder';
    case '/trips':
      return 'trips';
    case '/status':
      return 'status';
    case '/settings':
      return 'settings';
    case '/trash':
      return 'trash';
    default:
      return null;
  }
}
