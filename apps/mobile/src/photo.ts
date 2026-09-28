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
  const landscape = asset.width >= asset.height;
  const context = ImageManipulator.ImageManipulator.manipulate(asset.uri);
  if (Math.max(asset.width, asset.height) > LONG_EDGE) {
    context.resize(landscape ? { width: LONG_EDGE, height: null } : { width: null, height: LONG_EDGE });
  }
  const rendered = await context.renderAsync();
  const saved = await rendered.saveAsync({ format: ImageManipulator.SaveFormat.JPEG, compress: 0.8 });
  return saved.uri;
}
