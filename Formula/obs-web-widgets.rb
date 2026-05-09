class ObsWebWidgets < Formula
  include Language::Python::Virtualenv

  desc "Local web widgets for OBS Browser Source on macOS"
  homepage "https://github.com/wesleyel/obs-web-widgets"
  url "https://github.com/wesleyel/obs-web-widgets.git", tag: "v0.1.0"
  version "0.1.0"
  license "MIT"
  head "https://github.com/wesleyel/obs-web-widgets.git", branch: "main"

  depends_on "python@3.12"
  uses_from_macos "perl"

  resource "setuptools" do
    url "https://files.pythonhosted.org/packages/4f/db/cfac1baf10650ab4d1c111714410d2fbb77ac5a616db26775db562c8fab2/setuptools-82.0.1.tar.gz"
    sha256 "7d872682c5d01cfde07da7bccc7b65469d3dca203318515ada1de5eda35efbf9"
  end

  resource "wheel" do
    url "https://files.pythonhosted.org/packages/39/62/75f18a0f03b4219c456652c7780e4d749b929eb605c098ce3a5b6b6bc081/wheel-0.47.0.tar.gz"
    sha256 "cc72bd1009ba0cf63922e28f94d9d83b920aa2bb28f798a31d0691b02fa3c9b3"
  end

  def install
    venv = virtualenv_create(libexec, "python3.12")
    venv.pip_install resources
    venv.pip_install_and_link buildpath, build_isolation: false
  end

  service do
    run [opt_bin/"obs-web-widgets", "--no-open"]
    keep_alive true
    log_path var/"log/obs-web-widgets.log"
    error_log_path var/"log/obs-web-widgets.log"
  end

  test do
    assert_match "obs-web-widgets #{version}", shell_output("#{bin}/obs-web-widgets --version")
  end
end
