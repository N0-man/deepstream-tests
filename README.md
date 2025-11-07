# Deepstream Unit Testing

A quick attempt to write unit tests for a simple deepstream pipeline. There wasnt much guidance or reference from many of NVIDIA's repositories. Share feedback or suggestions if there are better ways to test. The sample pipeline code was leveraged from [NVDIA's deepstream_python_apps repo](https://github.com/NVIDIA-AI-IOT/deepstream_python_apps/blob/master/apps/deepstream-custom-binding-test/deepstream_custom_binding_test.py)

#### Dependencies

```bash
pip install -r requirements.txt
```

#### Run

```bash
pytest -v
```

#### Selective run

you can mark the test with `@pytest.mark.only` to selectively run them

```bash
pytest -vm only
```
