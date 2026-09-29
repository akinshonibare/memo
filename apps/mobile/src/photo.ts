import * as ImageManipulator from "expo-image-manipulator";
import * as ImagePicker from "expo-image-picker";

// Claude reads images best at up to 1568px on the long edge; anything larger is scaled
// down server-side anyway, so sending more only costs upload time and tokens.
const LONG_EDGE = 1568;

// Take or choose one photo, then resize and re-encode it as JPEG. JPEG because iPhone
// photos are HEIC by default and Claude vision doesn't read HEIC (ADR 0024).
export async function pickPhoto(source: "camera" | "library"): Promise<string | null> {
  const permission =
    source === "camera"
      ? await ImagePicker.requestCameraPermissionsAsync()
      : await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!permission.granted) throw new Error(`memo needs ${source === "camera" ? "camera" : "photo"} access for this`);

  const options: ImagePicker.ImagePickerOptions = { mediaTypes: ["images"], quality: 1 };
  const result =
    source === "camera"
      ? await ImagePicker.launchCameraAsync(options)
      : await ImagePicker.launchImageLibraryAsync(options);
  if (result.canceled || !result.assets[0]) return null;

  const asset = result.assets[0];
  return preparePhoto(asset.uri, asset.width, asset.height);
}

// Resize to the long edge and re-encode as JPEG. Shared from the picker and from the
// share sheet (a photo shared from Photos arrives as HEIC, just like a picked one).
export async function preparePhoto(uri: string, width?: number | null, height?: number | null): Promise<string> {
  const context = ImageManipulator.ImageManipulator.manipulate(uri);
  if (width && height && Math.max(width, height) > LONG_EDGE) {
    context.resize(width >= height ? { width: LONG_EDGE, height: null } : { width: null, height: LONG_EDGE });
  } else if (!width || !height) {
    context.resize({ width: LONG_EDGE, height: null }); // unknown size: cap it anyway
  }
  const rendered = await context.renderAsync();
  const saved = await rendered.saveAsync({ format: ImageManipulator.SaveFormat.JPEG, compress: 0.8 });
  return saved.uri;
}
