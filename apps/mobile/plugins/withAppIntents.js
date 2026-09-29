// Adds native/MemoIntents.swift (Siri and Shortcuts, ADR 0028) to the main app target.
// App Shortcuts have to be compiled into the app bundle itself for Siri to find them,
// so this can't be a separate extension target like the widget or share extension.
const fs = require("fs");
const path = require("path");
const { IOSConfig, withDangerousMod, withXcodeProject } = require("expo/config-plugins");

const FILE = "MemoIntents.swift";

function withAppIntents(config) {
  config = withDangerousMod(config, [
    "ios",
    async (cfg) => {
      const projectName = IOSConfig.XcodeUtils.getProjectName(cfg.modRequest.projectRoot);
      const dest = path.join(cfg.modRequest.platformProjectRoot, projectName, FILE);
      fs.copyFileSync(path.join(cfg.modRequest.projectRoot, "native", FILE), dest);
      return cfg;
    },
  ]);
  return withXcodeProject(config, (cfg) => {
    const project = cfg.modResults;
    const projectName = IOSConfig.XcodeUtils.getProjectName(cfg.modRequest.projectRoot);
    const filepath = `${projectName}/${FILE}`;
    if (!project.hasFile(filepath)) {
      IOSConfig.XcodeUtils.addBuildSourceFileToGroup({
        filepath,
        groupName: projectName,
        project,
        targetUuid: project.getFirstTarget().uuid,
      });
    }
    return cfg;
  });
}

module.exports = withAppIntents;
