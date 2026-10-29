// Generic person silhouette shown until the user uploads their own image.
const DEFAULT_AVATAR = 'data:image/svg+xml,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 60">' +
  '<rect width="60" height="60" fill="#c5cbd3"/>' +
  '<circle cx="30" cy="23" r="11" fill="#fff"/>' +
  '<path d="M8 60c0-13 10-20 22-20s22 7 22 20z" fill="#fff"/></svg>');

// The user's 60x60 avatar, or a default image when they have none.
export default function Avatar({ user, size = 60 }) {
  const style = { width: size, height: size };
  return <img className="avatar" src={user.avatar || DEFAULT_AVATAR} alt="" style={style} />;
}
